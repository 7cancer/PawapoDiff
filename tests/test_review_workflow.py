import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from fastapi.testclient import TestClient
import pytest
from pawapodiff.server import create_app
from pawapodiff.engine import compare, pair_text_shapes
from pawapodiff.llm import Connection, ReviewRequest, list_models, chat, fetch_reference
from test_comparison_table import shape, document


@contextmanager
def llm_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"models": [{"name": "test-vision"}]}
                    if self.path == "/api/tags"
                    else {"data": [{"id": "test-vision"}]}
                ).encode()
            )

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            answer = json.dumps(
                {
                    "summary": "接続試験用応答",
                    "issues": [
                        {
                            "page": 1,
                            "category": "translation",
                            "description": "用語を確認",
                            "evidence": "fixture",
                        }
                    ],
                },
                ensure_ascii=False,
            )
            result = (
                {"capabilities": ["completion", "vision"]}
                if self.path == "/api/show"
                else (
                    {"message": {"content": answer}}
                    if self.path == "/api/chat"
                    else {
                        "choices": [
                            {"message": {"content": answer}, "finish_reason": "stop"}
                        ]
                    }
                )
            )
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
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def wait_job(client, key, headers):
    for _ in range(200):
        data = client.get(f"/api/jobs/{key}", headers=headers).json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.02)
    raise AssertionError("job timeout")


def upload(client, tmp_path, headers, mode="consistency"):
    files = {}
    for side, text in [("left", "共通の日本語"), ("right", "English")]:
        p = Presentation()
        slide = p.slides.add_slide(p.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1)).text = text
        slide.notes_slide.notes_text_frame.text = text + " note"
        path = tmp_path / f"{side}.pptx"
        p.save(path)
        files[side] = (path.name, path.read_bytes())
    result = client.post(f"/api/jobs?mode={mode}", headers=headers, files=files)
    assert result.status_code == 200, result.text
    key = result.json()["id"]
    assert wait_job(client, key, headers)["status"] == "done"
    return key


def test_explicit_mapping_releases_targets_and_disables_unmatched_fallback():
    a, b = shape("a", "日本語文章"), shape("b", "別の文章")
    x, y = shape("x", "One"), shape("y", "Two")
    pairs = pair_text_shapes([a, b], [x, y], {"a": "y", "b": None})
    assert (
        next(target for source, target, _ in pairs if source and source["id"] == "a")
        == y
    )
    assert (
        next(target for source, target, _ in pairs if source and source["id"] == "b")
        is None
    )
    result = compare(
        document([[a, b]]), document([[x, y]]), mappings={1: {"a": "y", "b": None}}
    )
    assert (
        next(row for row in result["translations"] if row["source"] == "日本語文章")[
            "target"
        ]
        == "Two"
    )
    with pytest.raises(Exception, match="重複"):
        compare(
            document([[a, b]]), document([[x, y]]), mappings={1: {"a": "x", "b": "x"}}
        )


@pytest.mark.parametrize("provider", ["ollama", "openai"])
def test_real_http_protocols_and_image_payloads(provider, tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGB", (20, 10)).save(image)
    with llm_server() as (url, requests):
        config = ReviewRequest(
            provider=provider, base_url=url, model="test-vision", vision=True
        )
        assert list_models(config)[0]["id"] == "test-vision"
        result = chat(config, "プロンプト", {"source": "原文"}, [image, image])
        assert result["issues"][0]["page"] == 1
        path, body = requests[-1]
        if provider == "ollama":
            assert path == "/api/chat" and len(body["messages"][1]["images"]) == 2
        else:
            assert path == "/v1/chat/completions"
            assert body["messages"][1]["content"][1]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"
            )


def test_consistency_web_mapping_download_and_async_llm(tmp_path):
    def forbidden_renderer(*args, **kwargs):
        raise AssertionError("Must not render in consistency mode")

    with llm_server() as (url, requests), TestClient(
        create_app("token", tmp_path / "work", forbidden_renderer, mode="consistency"),
        base_url="http://127.0.0.1",
    ) as client:
        h = {"X-Pawapo-Token": "token"}
        assert client.get("/api/health", headers=h).json()["mode"] == "consistency"
        key = upload(client, tmp_path, h)
        state = wait_job(client, key, h)
        assert state["sides"] == [] and state["consistency"] is not None
        mapping = client.get(f"/api/jobs/{key}/mapping/1", headers=h).json()
        a = mapping["left"][0]["id"]
        b = mapping["right"][0]["id"]
        assert (
            client.put(
                f"/api/jobs/{key}/mapping/1", headers=h, json={"pairs": {a: b}}
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/jobs/{key}/mapping/1", headers=h, json={"pairs": {"missing": b}}
            ).status_code
            == 422
        )
        assert client.get(f"/api/jobs/{key}/notes.csv", headers=h).content.startswith(
            b"\xef\xbb\xbf"
        )
        assert (
            client.get(f"/api/jobs/{key}/consistency.csv", headers=h).status_code == 200
        )
        assert client.get(f"/api/jobs/{key}/report", headers=h).status_code == 409
        assert (
            client.post("/api/llm/models", headers=h, json={"base_url": url}).json()[
                "models"
            ][0]["id"]
            == "test-vision"
        )
        payload = {
            "base_url": url,
            "model": "test-vision",
            "tasks": ["translation", "notes", "overview"],
            "prompts": {"translation": "カスタム検査"},
            "api_key": "must-not-export",
        }
        started = client.post(f"/api/jobs/{key}/reviews", headers=h, json=payload)
        assert started.status_code == 200, started.text
        rid = started.json()["id"]
        for _ in range(200):
            result = client.get(f"/api/jobs/{key}/reviews/{rid}", headers=h).json()
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert result["status"] == "done", result
        assert len(result["results"]) == 3
        assert any(
            "カスタム検査" in json.dumps(body, ensure_ascii=False)
            for _, body in requests
        )
        download = client.get(f"/api/jobs/{key}/reviews/{rid}/download", headers=h)
        assert "must-not-export" not in download.text
        assert "カスタム検査" in download.text
        assert (
            client.post(
                f"/api/jobs/{key}/reviews",
                headers=h,
                json={**payload, "tasks": ["layout"], "vision": True},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/jobs/{key}/reviews", headers=h, json={**payload, "pages": [99]}
            ).status_code
            == 422
        )
        assert client.post("/api/llm/models", json={"base_url": url}).status_code == 403


def test_references_reject_private_urls_and_llm_credentials_in_url():
    with pytest.raises(ValueError):
        fetch_reference("http://127.0.0.1/secret")
    with pytest.raises(ValueError):
        fetch_reference("https://127.0.0.1/secret")
    with pytest.raises(ValueError):
        list_models(Connection(base_url="http://user:secret@localhost:11434"))


def test_page_image_review_and_overview_use_selected_pages_and_mapping(tmp_path):
    def renderer(path, dest):
        dest.mkdir(exist_ok=True)
        image = dest / "0001.png"
        Image.new("RGB", (200, 100), "white").save(image)
        return [image]

    with llm_server() as (url, requests), TestClient(
        create_app("token", tmp_path / "work", renderer), base_url="http://127.0.0.1"
    ) as client:
        h = {"X-Pawapo-Token": "token"}
        key = upload(client, tmp_path, h, mode="diff")
        mapping = client.get(f"/api/jobs/{key}/mapping/1", headers=h).json()
        source = mapping["left"][0]["id"]
        assert (
            client.put(
                f"/api/jobs/{key}/mapping/1", headers=h, json={"pairs": {source: None}}
            ).status_code
            == 200
        )
        response = client.post(
            f"/api/jobs/{key}/reviews",
            headers=h,
            json={
                "base_url": url,
                "model": "test-vision",
                "tasks": ["layout", "overview"],
                "vision": True,
                "pages": [1],
                "glossary": "公式名はExample Product",
                "references": [
                    {"url": "https://example.com/product", "text": "Example Product"}
                ],
            },
        )
        assert response.status_code == 200, response.text
        rid = response.json()["id"]
        for _ in range(200):
            review = client.get(f"/api/jobs/{key}/reviews/{rid}", headers=h).json()
            if review["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert review["status"] == "done", review
        assert [(r["task"], r["page"]) for r in review["results"]] == [
            ("layout", 1),
            ("overview", None),
        ]
        image_call = next(
            body
            for path, body in requests
            if path == "/api/chat" and "images" in body["messages"][1]
        )
        assert len(image_call["messages"][1]["images"]) == 2
        overview = [body for path, body in requests if path == "/api/chat"][-1][
            "messages"
        ][1]["content"]
        assert "image_reviewed_pages" in overview and "paired_text" in overview
        assert "Example Product" in overview
        assert (
            client.get(f"/api/jobs/{key}/report?categories=", headers=h).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/jobs/{key}/report?categories=unknown", headers=h
            ).status_code
            == 422
        )
