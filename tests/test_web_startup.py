"""Regressions seen only on machines without development-tool MIME associations."""

import mimetypes

import pytest
from fastapi.testclient import TestClient

from pawapodiff.server import create_app
from pawapodiff.web import frontend_assets, verify_frontend_http


@pytest.fixture
def bundle(tmp_path):
    directory = tmp_path / "frontend"
    directory.mkdir()
    (directory / "index.html").write_text(
        '<html><head><script type="module" src="/app.js"></script>'
        '<link rel="stylesheet" href="/app.css"></head><body></body></html>',
        encoding="utf-8",
    )
    (directory / "app.js").write_text("document.title = 'PawapoDiff';")
    (directory / "app.css").write_text("body { color: black; }")
    return directory


def test_browser_assets_ignore_incorrect_windows_mime_associations(
    bundle, tmp_path, monkeypatch
):
    mimetypes.init()
    for extension in (".js", ".css", ".html"):
        monkeypatch.setitem(mimetypes.types_map, extension, "text/plain")
    with TestClient(
        create_app("web-token", tmp_path / "work", frontend=bundle),
        base_url="http://127.0.0.1",
    ) as client:
        assert client.get("/").headers["content-type"].startswith("text/html")
        script = client.get("/app.js")
        assert script.headers["content-type"].startswith("text/javascript")
        assert script.headers["cache-control"] == "no-cache"
        cached = client.get(
            "/app.js", headers={"If-None-Match": script.headers["etag"]}
        )
        assert cached.status_code == 304
        assert cached.headers["content-type"].startswith("text/javascript")
        assert client.get("/app.css").headers["content-type"].startswith("text/css")
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/health").status_code == 403
        health = client.get("/api/health", headers={"X-Pawapo-Token": "web-token"})
        assert health.json()["status"] == "ready"


def test_bundle_check_finds_missing_script_not_just_index_html(bundle):
    assert frontend_assets(bundle) == ["/app.js", "/app.css"]
    (bundle / "app.js").unlink()
    with pytest.raises(RuntimeError, match="app.js"):
        frontend_assets(bundle)


@pytest.mark.parametrize("reference", ["https://example.com/app.js", "/../outside.js"])
def test_bundle_check_rejects_nonlocal_references(bundle, reference):
    (bundle / "index.html").write_text(
        f'<script type="module" src="{reference}"></script>'
    )
    with pytest.raises(RuntimeError, match="ローカル以外"):
        frontend_assets(bundle)


def test_packaged_runtime_probe_checks_real_http_responses(
    bundle, tmp_path, monkeypatch
):
    mimetypes.init()
    monkeypatch.setitem(mimetypes.types_map, ".js", "text/plain")
    monkeypatch.setitem(mimetypes.types_map, ".css", "text/plain")
    # A local probe must not send its authentication token to an environment proxy.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    app = create_app("web-token", tmp_path / "work", frontend=bundle)
    assert verify_frontend_http(app) == 2
