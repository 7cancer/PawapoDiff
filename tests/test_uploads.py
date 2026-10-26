import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.datastructures import Headers
from pawapodiff.uploads import (
    receive_presentations,
    DirectUploadParser,
    UploadLimitExceeded,
)
from pawapodiff.limits import MAX_UPLOAD_BYTES


def multipart(parts):
    body = b""
    for field, name, data in parts:
        body += f'--test\r\nContent-Disposition: form-data; name="{field}"'.encode()
        if name is not None:
            body += f'; filename="{name}"'.encode()
        body += b"\r\n\r\n" + data + b"\r\n"
    return body + b"--test--\r\n"


def request(body, chunk=7, length=None):
    chunks = [body[i : i + chunk] for i in range(0, len(body), chunk)]

    async def receive():
        data = chunks.pop(0) if chunks else b""
        return {"type": "http.request", "body": data, "more_body": bool(chunks)}

    headers = [(b"content-type", b"multipart/form-data; boundary=test")]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return Request({"type": "http", "headers": headers}, receive)


def test_streaming_upload_goes_directly_to_job_files_and_closes_handles(tmp_path):
    body = multipart(
        [
            ("left", "JP.pptx", b"12345"),
            ("right", "EN.pptx", b"67890"),
            ("threshold", None, b"0.5"),
        ]
    )
    assert asyncio.run(receive_presentations(request(body), tmp_path, limit=5)) == 0.5
    assert sorted(p.name for p in tmp_path.iterdir()) == ["left.pptx", "right.pptx"]
    assert (tmp_path / "left.pptx").read_bytes() == b"12345"
    assert (tmp_path / "right.pptx").read_bytes() == b"67890"
    # Windows cannot rename an open upload file; this verifies parser cleanup.
    (tmp_path / "left.pptx").rename(tmp_path / "closed.pptx")


def test_streaming_limit_aborts_and_closes_partial_files(tmp_path):
    body = multipart([("left", "JP.pptx", b"123456"), ("right", "EN.pptx", b"1")])
    with pytest.raises(HTTPException) as caught:
        asyncio.run(receive_presentations(request(body, 1), tmp_path, limit=5))
    assert caught.value.status_code == 413
    (tmp_path / "left.pptx").unlink()


def test_byte_counter_handles_15gb_without_32bit_overflow(tmp_path):
    async def empty():
        if False:
            yield b""

    parser = DirectUploadParser(Headers(), empty(), tmp_path)
    parser._current_part = SimpleNamespace(file=object(), field_name="left")
    parser.received["left"] = MAX_UPLOAD_BYTES - 1
    parser.on_part_data(b"x", 0, 1)
    assert parser.received["left"] == MAX_UPLOAD_BYTES
    with pytest.raises(UploadLimitExceeded):
        parser.on_part_data(b"x", 0, 1)


@pytest.mark.parametrize(
    "parts",
    [
        [("left", "JP.pptx", b"1")],
        [("left", "JP.pptx", b"1"), ("left", "duplicate.pptx", b"1")],
        [("left", "JP.ppt", b"1"), ("right", "EN.pptx", b"1")],
        [
            ("left", "JP.pptx", b"1"),
            ("right", "EN.pptx", b"1"),
            ("threshold", None, b"nan"),
        ],
    ],
)
def test_invalid_multipart_inputs_are_rejected(tmp_path, parts):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(receive_presentations(request(multipart(parts)), tmp_path, limit=5))
    assert caught.value.status_code == 422
    for path in tmp_path.iterdir():
        path.unlink()


def test_truncated_multipart_does_not_start_job(tmp_path):
    body = multipart([("left", "JP.pptx", b"1"), ("right", "EN.pptx", b"1")])[:-12]
    with pytest.raises(HTTPException):
        asyncio.run(receive_presentations(request(body), tmp_path, limit=5))


def test_oversized_content_length_is_rejected_before_receiving_body(tmp_path):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            receive_presentations(
                request(b"", length=2 * MAX_UPLOAD_BYTES + 1024**2 + 1), tmp_path
            )
        )
    assert caught.value.status_code == 413 and not list(tmp_path.iterdir())
