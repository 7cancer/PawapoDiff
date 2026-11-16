"""Browser tests use real PPTX analysis and explicit rendering/LLM fixtures."""

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import uvicorn
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pawapodiff.server import create_app
from pawapodiff.engine import inspect


class FixtureLLM(BaseHTTPRequestHandler):
    def do_GET(self):
        self.respond(
            {"models": [{"name": "fixture-vision"}], "data": [{"id": "fixture-vision"}]}
        )

    def do_POST(self):
        json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = json.dumps(
            {
                "summary": "API接続テスト応答（実LLMではありません）",
                "issues": [
                    {
                        "page": 1,
                        "category": "translation",
                        "severity": "low",
                        "description": "テスト用の確認候補",
                        "evidence": "テストデータ",
                        "suggestion": "応答の表示を確認",
                        "reference": "未確認",
                    }
                ],
            },
            ensure_ascii=False,
        )
        self.respond(
            {"capabilities": ["completion", "vision"]}
            if self.path == "/api/show"
            else {
                "message": {"content": text},
                "choices": [{"message": {"content": text}}],
            }
        )

    def respond(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, *args):
        pass


def fixture_renderer(path, dest):
    dest.mkdir(exist_ok=True)
    result = []
    for page in range(len(inspect(path)["slides"])):
        output = dest / f"{page+1:04}.png"
        image = Image.new("RGB", (800, 450), "#f5f7fb")
        ImageDraw.Draw(image).text(
            (30, 30), f"Browser test rendering fixture - page {page+1}", fill="#18324f"
        )
        image.save(output)
        result.append(output)
    return result


if __name__ == "__main__":
    for extension in (".js", ".css", ".html"):
        mimetypes.add_type("text/plain", extension)
    llm = ThreadingHTTPServer(("127.0.0.1", 8786), FixtureLLM)
    threading.Thread(target=llm.serve_forever, daemon=True).start()
    try:
        uvicorn.run(
            create_app("browser-test-token", renderer=fixture_renderer),
            host="127.0.0.1",
            port=8784,
            log_level="error",
        )
    finally:
        llm.shutdown()
        llm.server_close()
