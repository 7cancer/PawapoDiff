import json
import logging
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from fastapi.testclient import TestClient
import pytest
from pawapodiff import llm
from pawapodiff.llm import Connection, ReviewRequest, request_json, chat
from pawapodiff.llm_diagnostics import LLMError
from pawapodiff.diagnostics import configure_diagnostics
from pawapodiff.server import create_app
from test_review_workflow import upload

SECRET = "never-log-key-127f"
PRIVATE = "never-log-slide-and-prompt-84af"


@contextmanager
def response_server(status, payload, *, raw=False):
    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            if self.command == "POST":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode() if raw else json.dumps(payload).encode())

        do_GET = respond
        do_POST = respond

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize(
    "status,detail,code",
    [
        (404, "model not found", "LLM_NOT_FOUND"),
        (401, "denied", "LLM_AUTH_ERROR"),
        (500, "CUDA out of memory", "LLM_OUT_OF_MEMORY"),
        (400, "does not support images", "LLM_VISION_UNSUPPORTED"),
        (400, "context length exceeded", "LLM_CONTEXT_LIMIT"),
        (503, "runner failed", "LLM_HTTP_ERROR"),
    ],
)
def test_http_failure_codes_and_metadata_never_include_bodies_or_keys(
    caplog, status, detail, code
):
    caplog.set_level(logging.INFO, logger="pawapodiff")
    with response_server(
        status, {"error": detail + " " + SECRET + " " + PRIVATE}
    ) as url:
        config = Connection(base_url=url + "/private-base-path", api_key=SECRET)
        with pytest.raises(LLMError) as raised:
            request_json(
                config,
                "/api/chat",
                {"model": "qwen-test", "messages": [{"content": PRIVATE}]},
            )
    assert raised.value.code == code
    assert raised.value.request_id in caplog.text
    assert f'http_status="{status}"' in caplog.text
    assert "elapsed_s=" in caplog.text and "action=" in caplog.text
    assert SECRET not in caplog.text and PRIVATE not in caplog.text
    assert "private-base-path" not in caplog.text
    assert SECRET not in str(raised.value) and PRIVATE not in str(raised.value)


@pytest.mark.parametrize(
    "reason,code",
    [
        (TimeoutError(PRIVATE), "LLM_TIMEOUT"),
        (ConnectionRefusedError(111, PRIVATE), "LLM_CONNECTION_REFUSED"),
    ],
)
def test_network_failures_distinguish_timeout_and_refusal(
    monkeypatch, caplog, reason, code
):
    class Opener:
        def open(self, *args, **kwargs):
            raise URLError(reason)

    monkeypatch.setattr(llm, "build_opener", lambda *args: Opener())
    caplog.set_level(logging.INFO, logger="pawapodiff")
    with pytest.raises(LLMError) as raised:
        request_json(Connection(api_key=SECRET), "/api/tags")
    assert raised.value.code == code
    assert code in caplog.text and "exception=" in caplog.text
    assert PRIVATE not in caplog.text and SECRET not in caplog.text


def test_bad_json_and_model_output_warnings_hide_response_text(caplog):
    caplog.set_level(logging.INFO, logger="pawapodiff")
    with response_server(200, PRIVATE, raw=True) as url:
        with pytest.raises(LLMError, match="LLM_INVALID_RESPONSE"):
            request_json(Connection(base_url=url), "/api/tags")
    with response_server(
        200, {"message": {"content": PRIVATE}, "done_reason": "length"}
    ) as url:
        result = chat(
            ReviewRequest(base_url=url, model="qwen-test", api_key=SECRET),
            "custom " + PRIVATE,
            {"text": PRIVATE},
        )
    assert result["raw"] == PRIVATE
    assert result["warning"]
    assert "LLM_OUTPUT_TRUNCATED" in caplog.text and "LLM_OUTPUT_FORMAT" in caplog.text
    assert PRIVATE not in caplog.text and SECRET not in caplog.text


def test_console_logging_is_enabled_once_and_works_with_redirected_stderr(capsys):
    logger = logging.getLogger("pawapodiff")
    before = list(logger.handlers)
    try:
        configure_diagnostics(console=True)
        configure_diagnostics(console=True)
        assert (
            sum(bool(getattr(h, "_pawapo_console", False)) for h in logger.handlers)
            == 1
        )
        logger.info("日本語のコンソール診断テスト")
        assert capsys.readouterr().err.count("日本語のコンソール診断テスト") == 1
    finally:
        for handler in list(logger.handlers):
            if handler not in before and getattr(handler, "_pawapo_console", False):
                logger.removeHandler(handler)
                handler.close()


def test_async_failure_is_correlated_and_guide_is_downloadable(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pawapodiff")
    with response_server(404, {"error": "missing " + PRIVATE}) as url, TestClient(
        create_app("token", tmp_path / "work", mode="consistency"),
        base_url="http://127.0.0.1",
    ) as client:
        h = {"X-Pawapo-Token": "token"}
        guide = client.get("/api/llm/guide", headers=h)
        assert (
            guide.status_code == 200
            and "Ollama-guide.md" in guide.headers["content-disposition"]
        )
        assert "ollama pull" in guide.text
        assert client.get("/api/llm/guide").status_code == 403
        key = upload(client, tmp_path, h)
        response = client.post(
            f"/api/jobs/{key}/reviews",
            headers=h,
            json={
                "base_url": url,
                "model": "qwen-test",
                "api_key": SECRET,
                "tasks": ["translation"],
                "prompts": {"translation": PRIVATE},
            },
        )
        assert response.status_code == 200
        rid = response.json()["id"]
        for _ in range(200):
            result = client.get(f"/api/jobs/{key}/reviews/{rid}", headers=h).json()
            if result["status"] not in ("queued", "running"):
                break
            time.sleep(0.02)
        assert result["status"] == "error" and "LLM_NOT_FOUND" in result["message"]
        assert f'job="{key}"' in caplog.text and f'review="{rid}"' in caplog.text
        assert 'task="translation"' in caplog.text and "review_failure" in caplog.text
        assert PRIVATE not in caplog.text and SECRET not in caplog.text
        assert "共通の日本語" not in caplog.text


def test_invalid_request_details_do_not_echo_secret_headers(monkeypatch, caplog):
    class Opener:
        def open(self, *args, **kwargs):
            raise ValueError("Invalid header: " + SECRET)

    monkeypatch.setattr(llm, "build_opener", lambda *args: Opener())
    caplog.set_level(logging.INFO, logger="pawapodiff")
    with pytest.raises(LLMError) as raised:
        request_json(Connection(api_key=SECRET), "/api/tags")
    assert raised.value.code == "LLM_REQUEST_INVALID"
    assert SECRET not in str(raised.value) and SECRET not in caplog.text
