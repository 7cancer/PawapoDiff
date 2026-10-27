"""PowerPoint automation: isolated worker, private document copies, no application-wide Quit."""

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from PIL import Image
from .engine import validate
from .errors import DiagnosticError, office_diagnostic

logger = logging.getLogger(__name__)


class RenderError(DiagnosticError, RuntimeError):
    pass


def _worker_command(request_path):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--render-worker", str(request_path)]
    return [sys.executable, "-m", "pawapodiff", "--render-worker", str(request_path)]


def _read_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _run_worker(
    command, state_path, stderr_path, *, timeout=300, open_timeout=None, progress=None
):
    """Bound inactivity of our Python worker, never terminate PowerPoint processes."""
    last_progress = time.monotonic()
    last_stamp = None
    with Path(stderr_path).open("w+", encoding="utf-8", errors="replace") as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            while process.poll() is None:
                try:
                    stamp = Path(state_path).stat().st_mtime_ns
                except OSError:
                    stamp = None
                if stamp != last_stamp:
                    last_stamp, last_progress = stamp, time.monotonic()
                    if progress and stamp is not None:
                        try:
                            progress(
                                _read_state(state_path).get(
                                    "stage", "描画ワーカーの起動"
                                )
                            )
                        except Exception:
                            logger.warning(
                                "Could not report Office progress", exc_info=True
                            )
                stage = _read_state(state_path).get("stage", "描画ワーカーの起動")
                stage_timeout = (
                    (open_timeout or timeout)
                    if "資料を読み取り専用で開く" in stage
                    else timeout
                )
                if time.monotonic() - last_progress >= stage_timeout:
                    raise RenderError(
                        f"PowerPointの処理が{stage_timeout:g}秒間応答しないため中断しました（{stage}）。PowerPoint自体は終了していません。設定の復元ができていない可能性があるため、資料を保存してPowerPointを再起動してください。",
                        code="POWERPOINT_TIMEOUT",
                        stage=stage,
                        actions=[
                            "PowerPointのダイアログ・サインイン・権限保護を確認してください。",
                            "開いている資料を保存してPowerPointを再起動し、大きな資料は分割してお試しください。",
                        ],
                    )
                time.sleep(0.1)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        state = _read_state(state_path)
        if state.get("status") == "error":
            raise RenderError(
                state.get("message", "PowerPointの描画に失敗しました。"),
                **state.get("diagnostic", {}),
            )
        if process.returncode != 0 or state.get("status") != "done":
            stderr.seek(0)
            logger.error(
                "Office worker exited unexpectedly: exit=%s stderr=%s",
                process.returncode,
                stderr.read(8000),
            )
            raise RenderError(
                f"描画ワーカーが正常に完了しませんでした（終了コード{process.returncode}）。診断ログを確認してください。",
                code="WORKER_EXITED",
                stage=state.get("stage"),
                actions=[
                    "セキュリティ製品による子プロセスの制限や、メモリ不足を確認してください。"
                ],
            )
        return state


def render(path, destination, width=3840, *, timeout=300, progress=None):
    validate(path)
    if sys.platform != "win32":
        raise RenderError(
            "画像描画にはWindows版Microsoft PowerPointが必要です。この環境では実行できません。"
        )
    from .diagnostics import configure_diagnostics

    log_path = configure_diagnostics()
    try:
        destination = Path(destination).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        # A unique copy guarantees that closing the returned presentation cannot
        # close a document the user already had open, even when Office reuses its app.
        with tempfile.TemporaryDirectory(
            prefix="PawapoDiff-Office-", ignore_cleanup_errors=True
        ) as directory:
            work = Path(directory)
            private_source = work / "private-input.pptx"
            if progress:
                progress("描画専用の一時コピーを作成中")
            if shutil.disk_usage(work).free < Path(path).stat().st_size:
                raise RenderError(
                    "描画用の一時コピーを作る空き容量がありません。",
                    code="DISK_FULL",
                    actions=[
                        "Windowsの一時フォルダーがあるドライブの空き容量を増やしてください。"
                    ],
                )
            shutil.copyfile(path, private_source)
            request = work / "request.json"
            state_path = work / "state.json"
            request.write_text(
                json.dumps(
                    {
                        "mode": "render",
                        "source": str(private_source),
                        "destination": str(destination),
                        "width": width,
                        "state": str(state_path),
                    }
                ),
                encoding="utf-8",
            )
            state = _run_worker(
                _worker_command(request),
                state_path,
                work / "stderr.log",
                timeout=timeout,
                open_timeout=max(timeout, 900),
                progress=progress,
            )
            names = state.get("images", [])
            if not names or any(
                Path(name).name != name or not name.endswith(".png") for name in names
            ):
                raise RenderError(
                    "描画ワーカーから正しい画像一覧が返されませんでした。"
                )
            outputs = [destination / name for name in names]
            if not all(path.is_file() for path in outputs):
                raise RenderError("描画された画像が見つかりません。")
            return outputs
    except Exception as error:
        logger.exception("PowerPoint rendering failed")
        if isinstance(error, RenderError):
            raise RenderError(
                f"{error}\n診断ログ: {log_path}", **error.diagnostic
            ) from error
        if isinstance(error, (OSError, MemoryError)):
            raise
        raise RenderError(
            f"PowerPointの描画準備に失敗しました。{_error_detail(error)}\n診断ログ: {log_path}"
        ) from error


def _error_detail(error):
    details = []
    hresult = getattr(error, "hresult", None)
    if isinstance(hresult, int):
        details.append(f"HRESULT 0x{hresult & 0xFFFFFFFF:08X}")
    info = getattr(error, "excepinfo", None)
    if info:
        if len(info) > 2 and info[2]:
            details.append(str(info[2]).strip())
        if len(info) > 5 and isinstance(info[5], int) and info[5]:
            details.append(f"Office 0x{info[5] & 0xFFFFFFFF:08X}")
    message = getattr(error, "strerror", None) or str(error)
    if message and message not in details:
        details.append(message)
    if not details:
        details.append(type(error).__name__)
    return " / ".join(details)[:900]


def _retry_com(action, com, *, sleep=time.sleep):
    # Only retry RPC rejections that explicitly mean the server did not execute
    # the call. Do not retry invalid members, file errors, or arbitrary exceptions.
    for attempt in range(8):
        try:
            return action()
        except Exception as error:
            code = getattr(error, "hresult", 0) or 0
            if (code & 0xFFFFFFFF) not in (0x80010001, 0x8001010A) or attempt == 7:
                raise
            logger.info("Office busy; retry %s", attempt + 1)
            try:
                com.PumpWaitingMessages()
            except Exception:
                logger.warning("Could not pump Office messages", exc_info=True)
            sleep(min(2, 0.2 * 2**attempt))


def _render_powerpoint(
    path, destination, width, *, com, client, stage_callback=None, sleep=time.sleep
):
    """Document-scoped ownership using only documented PowerPoint members.

    Never read Application.HWND, modify Visible, call Application.Quit, or kill
    Office processes. DispatchEx may share an app with the user; close only the
    private document we opened, then restore its original application settings.
    """
    app = presentations = presentation = page_setup = slides = slide = None
    initialized = document_owned = False
    settings = []
    paths, cleanup_errors = [], []
    failure = None
    stage = "COMの初期化"

    def operation(label, action):
        nonlocal stage
        stage = label
        logger.info("Office stage: %s", label)
        if stage_callback:
            try:
                stage_callback(label)
            except Exception:
                logger.warning("Could not persist Office progress", exc_info=True)
        return _retry_com(action, com, sleep=sleep)

    try:
        operation("COMの初期化", com.CoInitialize)
        initialized = True
        app = operation(
            "PowerPointの起動", lambda: client.DispatchEx("PowerPoint.Application")
        )
        for name, desired in [("AutomationSecurity", 3), ("DisplayAlerts", 1)]:
            original = operation(
                f"PowerPointの設定取得: {name}", lambda name=name: getattr(app, name)
            )
            settings.append(
                (name, original)
            )  # Restore even if a setter fails after applying.
            operation(
                f"PowerPointの安全設定: {name}",
                lambda name=name, desired=desired: setattr(app, name, desired),
            )
        presentations = operation("資料一覧の取得", lambda: app.Presentations)
        presentation = operation(
            "資料を読み取り専用で開く",
            lambda: presentations.Open(str(Path(path).resolve()), -1, 0, 0),
        )
        full_name = operation("開いた一時資料の確認", lambda: presentation.FullName)
        if os.path.normcase(os.path.abspath(full_name)) != os.path.normcase(
            str(Path(path).resolve())
        ):
            raise RenderError(
                "指定した一時資料とは別の資料が返されました。利用者の資料を保護するため中断しました。"
            )
        document_owned = True
        # Microsoft's documented automation pattern restores security immediately
        # after Open, rather than leaving shared app settings changed during export.
        while settings:
            name, original = settings[-1]
            operation(
                f"PowerPointの設定復元: {name}",
                lambda name=name, original=original: setattr(app, name, original),
            )
            settings.pop()
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        page_setup = operation("スライド寸法の取得", lambda: presentation.PageSetup)
        slide_width = operation("スライド幅の取得", lambda: page_setup.SlideWidth)
        slide_height = operation("スライド高さの取得", lambda: page_setup.SlideHeight)
        if slide_width <= 0 or slide_height <= 0:
            raise RenderError("スライドの寸法が不正です。")
        height = max(1, round(width * slide_height / slide_width))
        slides = operation("スライド一覧の取得", lambda: presentation.Slides)
        count = operation("スライド数の取得", lambda: slides.Count)
        if count < 1:
            raise RenderError("描画するスライドがありません。")
        for i in range(1, count + 1):
            target = destination / f"{i:04}.png"
            slide = operation(
                f"スライド{i}の取得（{i}/{count}ページ）", lambda i=i: slides.Item(i)
            )
            operation(
                f"スライド{i}のPNG出力（{i}/{count}ページ）",
                lambda: slide.Export(str(target.resolve()), "PNG", width, height),
            )
            operation(f"スライド{i}の出力画像の確認", lambda: None)
            with Image.open(target) as image:
                image.load()
                if image.size != (width, height):
                    raise RenderError(
                        f"出力解像度が要求と一致しません（スライド{i}: 要求{width}×{height}、実際{image.width}×{image.height}）。"
                    )
            paths.append(target)
            slide = None
    except Exception as error:
        logger.exception("Office operation failed at %s", stage)
        diagnostic = office_diagnostic(error, stage)
        diagnostic["technical"] = _error_detail(error)
        failure = RenderError(
            f"PowerPointの処理に失敗しました（{stage}）。{_error_detail(error)}",
            **diagnostic,
        )
    finally:
        if document_owned and presentation is not None:
            try:
                operation("一時資料を閉じる", lambda: presentation.Close())
            except Exception as error:
                logger.exception("Could not close private document")
                cleanup_errors.append(f"一時資料を閉じる: {_error_detail(error)}")
        for name, original in reversed(settings):
            try:
                operation(
                    f"PowerPointの設定復元: {name}",
                    lambda name=name, original=original: setattr(app, name, original),
                )
            except Exception as error:
                logger.exception("Could not restore Office setting %s", name)
                cleanup_errors.append(f"{name}の復元: {_error_detail(error)}")
        # Release all proxy references before leaving the apartment. Keeping the
        # application alive or exiting it is left to Office, never a global Quit.
        slide = slides = page_setup = presentation = presentations = app = None
        if initialized:
            try:
                com.CoUninitialize()
            except Exception as error:
                logger.exception("COM cleanup failed")
                cleanup_errors.append(f"COMの解放: {_error_detail(error)}")
    if cleanup_errors:
        detail = " / ".join(cleanup_errors)
        failure = RenderError(
            f"{failure}\n後処理でも問題が発生しました: {detail}"
            if failure
            else f"画像生成後の後処理に失敗しました: {detail}",
            **(
                dict(failure.diagnostic, cleanup=detail)
                if failure
                else {
                    "code": "OFFICE_CLEANUP_FAILED",
                    "stage": stage,
                    "cleanup": detail,
                }
            ),
        )
    if failure:
        raise failure
    return paths
