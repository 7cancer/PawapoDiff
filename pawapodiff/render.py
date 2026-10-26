"""PowerPoint calls run only on a dedicated serialized worker thread."""

import sys
from pathlib import Path
from PIL import Image
from .engine import InputError, validate


class RenderError(RuntimeError):
    pass


def render(path, destination, width=3840):
    validate(path)
    if sys.platform != "win32":
        raise RenderError(
            "画像描画にはWindows版Microsoft PowerPointが必要です。この環境では実行できません。"
        )
    import pythoncom
    import win32com.client
    import psutil

    return _render_powerpoint(
        path,
        destination,
        width,
        com=pythoncom,
        client=win32com.client,
        processes=psutil,
    )


def _error_detail(error):
    """Surface actionable COM diagnostics locally, without losing the original stage."""
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
    if not details:
        details.append(f"{type(error).__name__}: {error}")
    return " / ".join(details)[:700]


def _window_process_id(hwnd):
    """Use a typed HWND boundary; Office returns an integer, not a PyHANDLE.

    A window handle must never be wrapped as an owning kernel HANDLE: closing
    that wrapper would call CloseHandle on a window we do not own.
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    lookup = user32.GetWindowThreadProcessId
    lookup.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    lookup.restype = wintypes.DWORD
    pid = wintypes.DWORD()
    thread_id = lookup(wintypes.HWND(int(hwnd)), ctypes.byref(pid))
    if not thread_id:
        raise ctypes.WinError(ctypes.get_last_error())
    return thread_id, pid.value


def _render_powerpoint(
    path,
    destination,
    width,
    *,
    com,
    client,
    processes,
    process_lookup=_window_process_id,
):
    # Office MsoTriState is an enum, not VARIANT_BOOL: True may marshal as the
    # unsupported value msoCTrue (1). Pass msoTrue (-1) and msoFalse (0) explicitly.
    # Positional arguments also support late-bound COM without generated wrappers.
    mso_true, mso_false = -1, 0
    com.CoInitialize()
    app = presentation = None
    owned = False
    stage = "既存のPowerPointプロセスの確認"
    try:
        existing = {
            p.pid
            for p in processes.process_iter(["name"])
            if (p.info["name"] or "").lower() == "powerpnt.exe"
        }
        stage = "PowerPointの起動"
        app = client.DispatchEx("PowerPoint.Application")
        stage = "PowerPointプロセスの所有権確認"
        _, pid = process_lookup(app.HWND)
        if not pid:
            raise RenderError(
                "PowerPointのプロセスを特定できないため、安全のため中断しました。"
            )
        owned = pid not in existing
        if not owned:
            raise RenderError(
                "PowerPointが既存プロセスを共有したため、安全のため中断しました。開いている資料を保存してPowerPointを終了し、再試行してください。"
            )
        stage = "PowerPointの安全設定"
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        app.DisplayAlerts = 1  # ppAlertsNone
        stage = "PowerPointの表示設定"
        app.Visible = mso_true
        stage = "資料を読み取り専用で開く"
        presentation = app.Presentations.Open(
            str(Path(path).resolve()), mso_true, mso_false, mso_false
        )
        stage = "出力先とスライド寸法の確認"
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        height = round(
            width
            * presentation.PageSetup.SlideHeight
            / presentation.PageSetup.SlideWidth
        )
        paths = []
        for i in range(1, presentation.Slides.Count + 1):
            stage = f"スライド{i}のPNG出力"
            target = destination / f"{i:04}.png"
            presentation.Slides.Item(i).Export(
                str(target.resolve()), "PNG", width, height
            )
            stage = f"スライド{i}の出力画像の確認"
            with Image.open(target) as image:
                if image.size != (width, height):
                    raise RenderError(
                        f"PowerPointの出力解像度が要求と一致しません（スライド{i}：要求{width}×{height}、実際{image.width}×{image.height}）。"
                    )
            paths.append(target)
        return paths
    except RenderError:
        raise
    except Exception as error:
        raise RenderError(
            f"PowerPointの処理に失敗しました（{stage}）。{_error_detail(error)}"
        ) from error
    finally:
        # A Close/Quit failure must not replace a useful Open/Export exception.
        import logging

        for label, obj, method in (
            ("Presentation.Close", presentation, "Close"),
            ("Application.Quit", app if owned else None, "Quit"),
        ):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception as error:
                    logging.getLogger(__name__).warning(
                        "%s: %s", label, _error_detail(error)
                    )
        com.CoUninitialize()


def export_images(paths, destination, preset="FHD", crop=None, width=1920):
    bounds = {"HD": (1280, 720), "FHD": (1920, 1080), "4K": (3840, 2160)}
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    for i, path in enumerate(paths, 1):
        with Image.open(path) as image:
            if preset == "custom":
                if not crop or len(crop) != 4:
                    raise InputError("切り抜き範囲を指定してください。")
                x, y, w, h = crop
                if not (
                    0 <= x < 1
                    and 0 <= y < 1
                    and w > 0
                    and h > 0
                    and x + w <= 1.000001
                    and y + h <= 1.000001
                ):
                    raise InputError("切り抜き範囲が画像外です。")
                box = (
                    round(x * image.width),
                    round(y * image.height),
                    round((x + w) * image.width),
                    round((y + h) * image.height),
                )
                if box[2] <= box[0] or box[3] <= box[1]:
                    raise InputError("切り抜き範囲が小さすぎます。")
                image = image.crop(box)
                size = (width, max(1, round(width * image.height / image.width)))
                if size[0] * size[1] > 40_000_000:
                    raise InputError(
                        "出力サイズが大きすぎます。範囲または幅を変更してください。"
                    )
            else:
                bw, bh = bounds[preset]
                ratio = min(bw / image.width, bh / image.height)
                size = (
                    max(1, round(image.width * ratio)),
                    max(1, round(image.height * ratio)),
                )
            out = destination / f"{i:04}.png"
            image.resize(size, Image.Resampling.LANCZOS).save(out)
            outputs.append(out)
    return outputs
