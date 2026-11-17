import argparse
import secrets
import socket
import threading
import webbrowser
import uvicorn
from .server import create_app


def main(mode="diff"):
    from .windows_runtime import configure_console

    configure_console()
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Verify packaged resources and Windows runtime without opening PowerPoint.",
    )
    parser.add_argument("--render-worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from .diagnostics import configure_diagnostics

    log_path = configure_diagnostics(console=True)
    import logging

    logging.getLogger(__name__).info(
        "診断ログ: %s", log_path or "保存先を利用できません（コンソールのみ）"
    )
    if args.render_worker:
        from .office_worker import run_worker

        return run_worker(args.render_worker)
    if args.check_runtime:
        import sys
        from pathlib import Path

        if sys.platform != "win32":
            parser.error("Runtime verification requires Windows.")
        if mode == "diff":
            import pythoncom
            import win32com.client

        bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        from .web import frontend_assets, verify_frontend_http

        frontend_assets(bundle / "frontend/dist")
        import json
        import tempfile
        from .render import _run_worker, _worker_command

        if mode == "diff":
            with tempfile.TemporaryDirectory(prefix="PawapoDiff-probe-") as directory:
                root = Path(directory)
                request = root / "request.json"
                request.write_text(
                    json.dumps({"mode": "probe", "state": str(root / "state.json")}),
                    encoding="utf-8",
                )
                state = _run_worker(
                    _worker_command(request),
                    root / "state.json",
                    root / "stderr.log",
                    timeout=30,
                )
                if not state.get("probe"):
                    parser.error("Office worker startup verification failed.")
        web_assets = verify_frontend_http(create_app(mode=mode))
        print(
            "PASS: Windows runtime and Web UI assets are available."
            if mode == "consistency"
            else "PASS: Windows runtime, assets and isolated worker startup are available."
        )
        from .windows_runtime import is_elevated
        from . import __version__

        print(
            json.dumps(
                {
                    "version": __version__,
                    "elevated": is_elevated(),
                    "runtime_check": "passed",
                    "web_assets_checked_over_http": web_assets,
                }
            )
        )
        return
    from .windows_runtime import (
        require_standard_user,
        ElevatedLaunchError,
        show_startup_error,
    )

    try:
        require_standard_user()
    except ElevatedLaunchError as error:
        import logging

        logging.getLogger(__name__).error("Elevated interactive launch rejected")
        show_startup_error(str(error))
        return 1
    token = secrets.token_urlsafe(32)
    from .web import frontend_assets, frontend_directory

    try:
        frontend_assets(frontend_directory())
    except (OSError, RuntimeError, UnicodeError) as error:
        import logging

        logging.getLogger(__name__).exception("Bundled frontend validation failed")
        show_startup_error(str(error))
        return 1
    app = create_app(token, mode=mode)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
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
    logging.getLogger(__name__).info(
        "Web UI origin=http://127.0.0.1:%s mode=%s（認証トークンは表示しません）",
        port,
        mode,
    )
    print("PawapoDiff: 終了するにはこのウィンドウで Ctrl+C を押してください。")
    server.run(sockets=[sock])


if __name__ == "__main__":
    raise SystemExit(main())
