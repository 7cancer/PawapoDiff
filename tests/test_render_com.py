"""Contract tests for COM calls. These do not claim actual PowerPoint execution."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pawapodiff.render import _render_powerpoint, RenderError


class OfficeFailure(Exception):
    hresult = -2147352567
    excepinfo = (0, "PowerPoint", "Office operation failed", None, 0, -2147024809)


def office(tmp_path, *, fail=None, shared=False, cleanup_fails=False):
    calls = []

    def cleanup(label):
        calls.append(label)
        if cleanup_fails:
            raise RuntimeError("cleanup error")

    def export(path, kind, width, height):
        calls.append("export")
        if fail == "export":
            raise OfficeFailure()
        assert kind == "PNG"
        Image.new("RGB", (width, height)).save(path)

    presentation = SimpleNamespace(
        PageSetup=SimpleNamespace(SlideHeight=540, SlideWidth=960),
        Slides=SimpleNamespace(Count=3, Item=lambda n: SimpleNamespace(Export=export)),
        Close=lambda: cleanup("close"),
    )

    def open_presentation(path, readonly, untitled, with_window, /):
        calls.append("open")
        # Reject bools even though Python considers True == 1 and False == 0.
        assert all(type(value) is int for value in (readonly, untitled, with_window))
        assert (readonly, untitled, with_window) == (-1, 0, 0)
        assert Path(path).is_absolute()
        assert app.AutomationSecurity == 3 and app.DisplayAlerts == 1
        assert type(app.Visible) is int and app.Visible == -1
        if fail == "open":
            raise OfficeFailure()
        return presentation

    app = SimpleNamespace(
        HWND=123,
        Presentations=SimpleNamespace(Open=open_presentation),
        Quit=lambda: cleanup("quit"),
    )

    def process_lookup(hwnd):
        if type(hwnd) is not int:
            raise TypeError("Expected an integer HWND, not a method")
        assert hwnd == 123
        return (1, 42)

    dependencies = dict(
        com=SimpleNamespace(
            CoInitialize=lambda: calls.append("init"),
            CoUninitialize=lambda: calls.append("uninit"),
        ),
        client=SimpleNamespace(DispatchEx=lambda name: app),
        process_lookup=process_lookup,
        processes=SimpleNamespace(
            process_iter=lambda attrs: (
                [SimpleNamespace(pid=42, info={"name": "POWERPNT.EXE"})]
                if shared
                else []
            )
        ),
    )
    return calls, dependencies


def test_positional_office_enum_arguments_and_all_pages(tmp_path):
    calls, dependencies = office(tmp_path)
    paths = _render_powerpoint(
        tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
    )
    assert len(paths) == 3
    assert calls == [
        "init",
        "open",
        "export",
        "export",
        "export",
        "close",
        "quit",
        "uninit",
    ]
    for path in paths:
        with Image.open(path) as image:
            assert image.size == (160, 90)


@pytest.mark.parametrize(
    "fail,stage", [("open", "読み取り専用"), ("export", "スライド1のPNG出力")]
)
def test_failure_has_stage_hresult_and_office_detail(tmp_path, fail, stage):
    calls, dependencies = office(tmp_path, fail=fail, cleanup_fails=True)
    with pytest.raises(RenderError) as caught:
        _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
    message = str(caught.value)
    assert stage in message
    assert "0x80020009" in message and "0x80070057" in message
    assert "Office operation failed" in message
    assert "cleanup error" not in message
    assert calls[-1] == "uninit" and "quit" in calls


def test_existing_powerpoint_is_not_opened_or_closed(tmp_path):
    calls, dependencies = office(tmp_path, shared=True)
    with pytest.raises(RenderError, match="既存プロセス"):
        _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
    assert calls == ["init", "uninit"]


def test_activation_failure_is_diagnosed_and_com_is_released(tmp_path):
    calls, dependencies = office(tmp_path)

    def fail_activation(name):
        raise OfficeFailure()

    dependencies["client"].DispatchEx = fail_activation
    with pytest.raises(RenderError, match="PowerPointの起動"):
        _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
    assert calls == ["init", "uninit"]


def test_unknown_process_is_not_quit(tmp_path):
    calls, dependencies = office(tmp_path)
    dependencies["process_lookup"] = lambda hwnd: (0, 0)
    with pytest.raises(RenderError, match="特定できない"):
        _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
    assert calls == ["init", "uninit"]


@pytest.mark.skipif(
    __import__("sys").platform != "win32", reason="Requires native Windows HWND APIs"
)
@pytest.mark.parametrize("hwnd_as_method", [False, True])
def test_office_hwnd_resolves_with_real_windows_api(tmp_path, hwnd_as_method):
    """Runs in Windows CI without Office, using a real window owned by this test."""
    import ctypes
    from ctypes import wintypes
    import os
    from pawapodiff.render import _window_process_id

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "PawapoDiff HWND test", 0, 0, 0, 1, 1, None, None, None, None
    )
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # Exercise the entire ownership path against a real Windows handle,
        # including the late-bound method that triggered the reported failure.
        calls, dependencies = office(tmp_path)
        app = dependencies["client"].DispatchEx("PowerPoint.Application")
        getter_calls = []

        class LateBoundApplication:
            def HWND(self):
                getter_calls.append("HWND")
                return int(hwnd)

        app.HWND = LateBoundApplication().HWND if hwnd_as_method else int(hwnd)

        def real_process_lookup(value):
            thread_id, pid = _window_process_id(value)
            assert thread_id > 0
            assert pid == os.getpid()
            return thread_id, pid

        dependencies["process_lookup"] = real_process_lookup
        paths = _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
        assert len(paths) == 3
        assert getter_calls == (["HWND"] if hwnd_as_method else [])
        assert "quit" in calls
        with pytest.raises(OSError):
            _window_process_id(0)
    finally:
        assert user32.DestroyWindow(hwnd)


@pytest.mark.parametrize("shared", [False, True])
def test_bound_hwnd_getter_is_called_once_before_ownership_lookup(tmp_path, shared):
    calls, dependencies = office(tmp_path, shared=shared)
    app = dependencies["client"].DispatchEx("PowerPoint.Application")
    getter_calls = []

    class LateBoundApplication:
        def HWND(self):
            getter_calls.append("HWND")
            return 123

    app.HWND = LateBoundApplication().HWND
    if shared:
        with pytest.raises(RenderError, match="既存プロセス"):
            _render_powerpoint(
                tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
            )
        assert calls == ["init", "uninit"]
    else:
        paths = _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
        assert len(paths) == 3
        assert "open" in calls and "close" in calls and "quit" in calls
    assert getter_calls == ["HWND"]


def test_failed_hwnd_getter_does_not_open_or_quit_unidentified_app(tmp_path):
    calls, dependencies = office(tmp_path)
    app = dependencies["client"].DispatchEx("PowerPoint.Application")

    class LateBoundApplication:
        def HWND(self):
            raise OfficeFailure()

    app.HWND = LateBoundApplication().HWND
    with pytest.raises(RenderError, match="所有権確認"):
        _render_powerpoint(
            tmp_path / "input.pptx", tmp_path / "out", 160, **dependencies
        )
    assert calls == ["init", "uninit"]
