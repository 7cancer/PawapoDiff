"""Private subprocess entry point; never starts the browser or web server."""

import json
import logging
import os
from pathlib import Path


def write_state(path, data):
    path = Path(path)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def run_worker(request_path):
    from .diagnostics import configure_diagnostics
    from .render import _render_powerpoint, _error_detail
    from .engine import validate
    from .errors import describe

    configure_diagnostics()
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    state_path = request["state"]
    stage = "描画ワーカーの初期化"

    def progress(label):
        nonlocal stage
        stage = label
        write_state(state_path, {"status": "running", "stage": label})

    try:
        progress(stage)
        import pythoncom
        import win32com.client

        if request.get("mode") == "probe":
            write_state(state_path, {"status": "done", "probe": True})
            return 0
        if request.get("mode") != "render":
            raise ValueError("Unknown Office worker mode")
        from .windows_runtime import require_standard_user, ElevatedLaunchError
        from .render import RenderError

        try:
            require_standard_user()
        except ElevatedLaunchError as error:
            raise RenderError(
                str(error),
                code="ELEVATED_PROCESS",
                stage="PowerPoint連携の実行権限の確認",
                actions=[
                    "PawapoDiffとPowerPointを通常権限で起動してください。管理者として実行する互換性設定も確認してください。"
                ],
            ) from error
        validate(request["source"])
        images = _render_powerpoint(
            request["source"],
            request["destination"],
            request["width"],
            com=pythoncom,
            client=win32com.client,
            stage_callback=progress,
        )
        write_state(state_path, {"status": "done", "images": [p.name for p in images]})
        return 0
    except Exception as error:
        logging.getLogger(__name__).exception("Office worker failed at %s", stage)
        write_state(
            state_path,
            {
                "status": "error",
                "stage": stage,
                "message": _error_detail(error),
                "diagnostic": describe(error),
            },
        )
        return 1
