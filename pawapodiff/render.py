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
    import win32process
    import psutil

    pythoncom.CoInitialize()
    app = presentation = None
    existing = {
        p.pid
        for p in psutil.process_iter(["name"])
        if (p.info["name"] or "").lower() == "powerpnt.exe"
    }
    owned = False
    try:
        try:
            app = win32com.client.DispatchEx("PowerPoint.Application")
        except Exception as e:
            raise RenderError(
                "PowerPointを起動できません。Windows版PowerPointの導入と初回起動を確認してください。"
            ) from e
        _, pid = win32process.GetWindowThreadProcessId(app.HWND)
        owned = pid not in existing
        if not owned:
            raise RenderError(
                "PowerPointが既存プロセスを共有したため、安全のため中断しました。開いている資料を保存してPowerPointを終了し、再試行してください。"
            )
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        app.DisplayAlerts = 1  # ppAlertsNone
        presentation = app.Presentations.Open(
            str(Path(path).resolve()), ReadOnly=True, Untitled=False, WithWindow=False
        )
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        height = round(
            width
            * presentation.PageSetup.SlideHeight
            / presentation.PageSetup.SlideWidth
        )
        for i in range(1, presentation.Slides.Count + 1):
            target = destination / f"{i:04}.png"
            presentation.Slides(i).Export(str(target.resolve()), "PNG", width, height)
            with Image.open(target) as im:
                if im.width != width or im.height != height:
                    raise RenderError("PowerPointの出力解像度が要求と一致しません。")
        return sorted(destination.glob("*.png"))
    except RenderError:
        raise
    except Exception as e:
        raise RenderError(
            "PowerPointのスライド描画に失敗しました。資料を手動で開けるか確認してください。"
        ) from e
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        finally:
            try:
                if app is not None and owned:
                    app.Quit()
            finally:
                pythoncom.CoUninitialize()


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
