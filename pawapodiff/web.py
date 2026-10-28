"""Serve bundled browser assets independently of Windows MIME registry entries."""

from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit

from fastapi.staticfiles import StaticFiles

ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
}


class BrowserFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            suffix = Path(path).suffix.lower() if path not in ("", ".") else ".html"
            if media_type := ASSET_TYPES.get(suffix):
                response.headers["content-type"] = media_type
            response.headers["cache-control"] = "no-cache"
        return response


class _References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []
        self.modules = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.paths.append(attrs["src"])
            if attrs.get("type") == "module":
                self.modules.append(attrs["src"])
        elif tag == "link" and attrs.get("rel") == "stylesheet" and attrs.get("href"):
            self.paths.append(attrs["href"])


def frontend_directory():
    return (
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        / "frontend/dist"
    )


def frontend_assets(directory):
    """Check actual JS/CSS references, rather than only the HTML entry point."""
    directory = Path(directory).resolve()
    index = directory / "index.html"
    if not index.is_file():
        raise RuntimeError(f"画面のHTMLが同梱されていません: {index}")
    parser = _References()
    parser.feed(index.read_text(encoding="utf-8"))
    if not parser.modules:
        raise RuntimeError("画面のJavaScriptがHTMLに指定されていません。")
    for reference in parser.paths:
        url = urlsplit(reference)
        path = directory / unquote(url.path).lstrip("/")
        if url.scheme or url.netloc or not path.resolve().is_relative_to(directory):
            raise RuntimeError("画面にローカル以外のファイル参照があります。")
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"画面ファイルが欠落しているか空です: {path}")
    return parser.paths


def verify_frontend_http(app):
    """Probe the packaged app over real loopback HTTP without requiring a browser."""
    import json
    import socket
    import threading
    import time
    from urllib.request import Request, build_opener, ProxyHandler
    import uvicorn

    assets = frontend_assets(app.state.frontend)
    urlopen = build_opener(ProxyHandler({})).open
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("画面のHTTPサーバーを起動できません。")
                time.sleep(0.02)
            for path in ["/", *assets]:
                url = origin + "/" + path.lstrip("/")
                with urlopen(url, timeout=10) as response:
                    expected = ASSET_TYPES.get(
                        Path(urlsplit(url).path).suffix, "text/html"
                    )
                    if response.headers.get_content_type() != expected.split(";")[0]:
                        raise RuntimeError(f"画面ファイルの配信形式が不正です: {path}")
                    if not response.read(1):
                        raise RuntimeError(f"画面ファイルが空です: {path}")
            request = Request(
                origin + "/api/health", headers={"X-Pawapo-Token": app.state.token}
            )
            with urlopen(request, timeout=10) as response:
                if json.load(response).get("status") != "ready":
                    raise RuntimeError("画面のAPI起動確認に失敗しました。")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("画面のHTTP検証を終了できませんでした。")
    return len(assets)
