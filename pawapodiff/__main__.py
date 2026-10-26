import argparse
import secrets
import socket
import threading
import webbrowser
import uvicorn
from .server import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Verify packaged resources and Windows runtime without opening PowerPoint.",
    )
    args = parser.parse_args()
    if args.check_runtime:
        import sys
        from pathlib import Path

        if sys.platform != "win32":
            parser.error("Runtime verification requires Windows.")
        import pythoncom
        import win32com.client
        import psutil

        bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        if not (bundle / "frontend/dist/index.html").is_file():
            parser.error("Packaged frontend assets are missing.")
        print("PASS: Windows runtime modules and frontend assets are available.")
        return
    token = secrets.token_urlsafe(32)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_app(token)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )

    def open_browser():
        import time

        while not server.started and not server.should_exit:
            time.sleep(0.1)
        if server.started:
            webbrowser.open(f"http://127.0.0.1:{port}/#token={token}")

    if not args.no_browser:
        threading.Thread(target=open_browser, daemon=True).start()
    print("PawapoDiff: 終了するにはこのウィンドウで Ctrl+C を押してください。")
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
