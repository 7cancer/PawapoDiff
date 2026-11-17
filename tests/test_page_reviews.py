import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import threading
import time
import pytest
from pydantic import ValidationError
from PIL import Image
from pptx import Presentation
from fastapi.testclient import TestClient
from pawapodiff import llm
from pawapodiff.llm import ReviewRequest, Connection, LLMError
from pawapodiff.server import create_app
from test_comparison_table import document, shape


@contextmanager
def gated_server():
    calls = []
    second = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/show":
                result = {"capabilities": ["vision"]}
            else:
                message = body["messages"][1]
                payload = json.loads(message["content"].rsplit("\n", 1)[-1])
                calls.append((payload, message.get("images", [])))
                page = payload["pages"][0]["page"]
                if page == 2:
                    second.set()
                    release.wait(10)
                result = {
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": f"page {page}",
                                "issues": [{"page": 999, "description": "fixture"}],
                            }
                        )
                    }
                }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls, second, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_pairs_one_page_at_a_time_and_exposes_completed_page(tmp_path):
    def renderer(path, dest):
        dest.mkdir()
        result = []
        for page in range(1, 4):
            color = (
                (50 * page, 10, 10) if path.name == "left.pptx" else (10, 50 * page, 10)
            )
            out = dest / f"{page:04}.png"
            Image.new("RGB", (8, 8), color).save(out)
            result.append(out)
        return result

    with gated_server() as (url, calls, second, release), TestClient(
        create_app("token", tmp_path / "work", renderer), base_url="http://127.0.0.1"
    ) as client:
        headers = {"X-Pawapo-Token": "token"}
        files = {}
        for side in ("left", "right"):
            deck = Presentation()
            for page in range(1, 4):
                slide = deck.slides.add_slide(deck.slide_layouts[6])
                slide.shapes.add_textbox(0, 0, 100000, 100000).text = (
                    f"{side} page {page}"
                )
            out = BytesIO()
            deck.save(out)
            files[side] = (f"{side}.pptx", out.getvalue())
        key = client.post("/api/jobs", headers=headers, files=files).json()["id"]
        for _ in range(200):
            job = client.get(f"/api/jobs/{key}", headers=headers).json()
            if job["status"] in ("done", "error"):
                break
            time.sleep(0.02)
        assert job["status"] == "done", job
        rid = client.post(
            f"/api/jobs/{key}/reviews",
            headers=headers,
            json={
                "base_url": url,
                "model": "fixture-vision",
                "tasks": ["layout"],
                "vision": True,
                "pages": [3, 1, 2, 2],
            },
        ).json()["id"]
        try:
            assert second.wait(5)
            state = client.get(f"/api/jobs/{key}/reviews/{rid}", headers=headers).json()
            assert state["status"] == "running"
            assert [item["page"] for item in state["results"]] == [1]
            assert [payload["pages"][0]["page"] for payload, _ in calls] == [1, 2]
        finally:
            release.set()
        for _ in range(200):
            state = client.get(f"/api/jobs/{key}/reviews/{rid}", headers=headers).json()
            if state["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert state["status"] == "done", state
        assert [item["page"] for item in state["results"]] == [1, 2, 3]
        assert [item["issues"][0]["page"] for item in state["results"]] == [1, 2, 3]
        for page, (payload, images) in enumerate(calls, 1):
            assert len(payload["pages"]) == 1 and len(images) == 2
            assert payload["image_order"] == ["JP", "EN"]
            for side, encoded in enumerate(images):
                with Image.open(BytesIO(base64.b64decode(encoded))) as image:
                    pixel = image.getpixel((0, 0))
                expected = (50 * page, 10, 10) if side == 0 else (10, 50 * page, 10)
                assert all(abs(a - b) < 4 for a, b in zip(pixel, expected))
            assert all(
                f"left page {other}" not in json.dumps(payload)
                for other in range(1, 4)
                if other != page
            )


def test_every_page_finishes_selected_checks_before_next_and_overview_is_compact(
    monkeypatch,
):
    docs = {
        "left": document([[shape("a", "売上高")], [shape("a", "売上高")]]),
        "right": document([[shape("a", "Sales")], [shape("a", "Revenue")]]),
    }
    calls = []
    order = []

    def chat(config, prompt, payload, images=()):
        page = payload["pages"][0]["page"] if "pages" in payload else None
        calls.append((payload, list(images)))
        order.append(("request", page))
        return {
            "summary": "summary",
            "issues": [],
            "raw": "duplicate raw output",
            "warning": "",
        }

    def progress(message, item=None):
        if item is not None:
            order.append(("published", item["page"]))

    monkeypatch.setattr(llm, "chat", chat)
    monkeypatch.setattr(llm, "model_info", lambda *args: {"vision": True})
    result = llm.run_review(
        ReviewRequest(
            model="fixture",
            vision=True,
            tasks=["layout", "translation", "notes", "overview"],
        ),
        docs,
        {"left": ["JP1", "JP2"], "right": ["EN1", "EN2"]},
        progress,
        lambda: False,
    )
    assert [(r["task"], r["page"]) for r in result] == [
        ("layout", 1),
        ("translation", 1),
        ("notes", 1),
        ("layout", 2),
        ("translation", 2),
        ("notes", 2),
        ("overview", None),
    ]
    assert order == [
        event
        for page in [1, 1, 1, 2, 2, 2, None]
        for event in [("request", page), ("published", page)]
    ]
    assert calls[0][1] == ["JP1", "EN1"] and calls[3][1] == ["JP2", "EN2"]
    summary = calls[-1][0]
    assert calls[-1][1] == [] and "pages" not in summary
    assert all("raw" not in finding for finding in summary["page_findings"])
    assert summary["cross_page_consistency_candidates"][0]["source"] == "売上高"
    assert {
        v["target"] for v in summary["cross_page_consistency_candidates"][0]["variants"]
    } == {"Sales", "Revenue"}


def test_overview_only_still_reviews_each_page_first(monkeypatch):
    docs = {
        "left": document([[shape("a", "日本語")], [shape("a", "別の文章")]]),
        "right": document([[shape("a", "English")], [shape("a", "Another")]]),
    }
    monkeypatch.setattr(
        llm,
        "chat",
        lambda *args, **kwargs: {
            "summary": "ok",
            "issues": [],
            "raw": "ok",
            "warning": "",
        },
    )
    result = llm.run_review(
        ReviewRequest(model="fixture", tasks=["overview"]),
        docs,
        {},
        lambda *args: None,
        lambda: False,
    )
    assert [r["page"] for r in result] == [1, 2, None]


def test_large_output_configuration_is_bounded_and_read_in_chunks(monkeypatch):
    ReviewRequest(model="fixture", max_tokens=8192000, timeout=86400)
    with pytest.raises(ValidationError):
        ReviewRequest(model="fixture", max_tokens=8192001)
    sizes = []
    data = json.dumps({"value": "x" * (9 * 1024 * 1024)}).encode()

    class Response(BytesIO):
        status = 200

        def read(self, size=-1):
            sizes.append(size)
            return super().read(size)

    class Opener:
        def open(self, *args, **kwargs):
            return Response(data)

    monkeypatch.setattr(llm, "build_opener", lambda *args: Opener())
    with pytest.raises(LLMError, match="LLM_RESPONSE_TOO_LARGE"):
        llm.request_json(Connection(), "/api/tags")
    assert (
        len(
            llm.request_json(
                ReviewRequest(model="fixture", max_tokens=8192000), "/api/chat"
            )["value"]
        )
        == 9 * 1024 * 1024
    )
    assert max(sizes) <= 65536
