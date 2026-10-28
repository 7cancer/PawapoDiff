"""Browser-test backend with deliberately broken OS MIME associations; no Office."""

import mimetypes
from pathlib import Path
import sys

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pawapodiff.server import create_app

mimetypes.init()
for extension in (".js", ".css", ".html"):
    mimetypes.add_type("text/plain", extension)
uvicorn.run(
    create_app("browser-test-token"), host="127.0.0.1", port=8784, log_level="error"
)
