"""Document ownership, documented COM contract, bounded retries and cleanup failures."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pawapodiff.render import _render_powerpoint, _retry_com, RenderError


class OfficeFailure(Exception):
    def __init__(self, code=0x80020003, description="Member not found"):
        self.hresult = code - 2**32
        self.strerror = description
        self.excepinfo = None
        super().__init__(description)


def office(*, failures=()):
    failures = set(failures)
    calls = []
    state = {"security": 2, "alerts": 2, "source": None, "user_closed": False}

    def check(label):
        calls.append(label)
        if label in failures:
            raise OfficeFailure(description=label + " failed")

    class App:
        def __getattribute__(self, name):
            if name in ("HWND", "Visible", "Quit"):
                calls.append("FORBIDDEN:" + name)
                raise OfficeFailure()
            return object.__getattribute__(self, name)

        @property
        def AutomationSecurity(self):
            check("security_read")
            return state["security"]

        @AutomationSecurity.setter
        def AutomationSecurity(self, value):
            check("security_set" if value == 3 else "security_restore")
            state["security"] = value

        @property
        def DisplayAlerts(self):
            check("alerts_read")
            return state["alerts"]

        @DisplayAlerts.setter
        def DisplayAlerts(self, value):
            check("alerts_set" if value == 1 else "alerts_restore")
            state["alerts"] = value

        @property
        def Presentations(self):
            check("presentations")
            return presentations

    class Presentations:
        def Open(self, path, readonly, untitled, with_window, /):
            check("open")
            assert all(type(v) is int for v in (readonly, untitled, with_window))
            assert (readonly, untitled, with_window) == (-1, 0, 0)
            assert state["security"] == 3 and state["alerts"] == 1
            state["source"] = path
            return presentation

    class Presentation:
        @property
        def FullName(self):
            check("fullname")
            return (
                str(Path(state["source"]).with_name("user-document.pptx"))
                if "wrong_document" in failures
                else state["source"]
            )

        @property
        def PageSetup(self):
            check("size")
            return SimpleNamespace(SlideWidth=960, SlideHeight=540)

        @property
        def Slides(self):
            return slides

        def Close(self):
            check("close")

    class Slides:
        @property
        def Count(self):
            check("count")
            return 0 if "empty" in failures else 3

        def Item(self, index):
            check("item")
            return slide

    class Slide:
        def Export(self, path, kind, width, height):
            check("export")
            # Restore shared app settings immediately after Open, before rendering.
            assert state["security"] == 2 and state["alerts"] == 2
            if "missing_image" not in failures:
                Image.new(
                    "RGB", (width + 1 if "wrong_size" in failures else width, height)
                ).save(path)

    app = App()
    presentations = Presentations()
    presentation = Presentation()
    slides = Slides()
    slide = Slide()

    def dispatch(name):
        check("activate")
        assert name == "PowerPoint.Application"
        return app

    dependencies = {
        "com": SimpleNamespace(
            CoInitialize=lambda: check("init"),
            CoUninitialize=lambda: check("uninit"),
            PumpWaitingMessages=lambda: check("pump"),
        ),
        "client": SimpleNamespace(DispatchEx=dispatch),
        "sleep": lambda _: None,
    }
    return calls, state, dependencies


def test_success_without_hwnd_or_application_quit_preserves_user_settings(tmp_path):
    calls, state, dependencies = office()
    result = _render_powerpoint(
        tmp_path / "private.pptx", tmp_path / "out", 160, **dependencies
    )
    assert len(result) == 3
    assert state["security"] == 2 and state["alerts"] == 2
    assert not any(c.startswith("FORBIDDEN") for c in calls)
    assert calls.count("open") == calls.count("close") == calls.count("uninit") == 1
    assert not state["user_closed"]
    for path in result:
        with Image.open(path) as image:
            assert image.size == (160, 90)


@pytest.mark.parametrize(
    "failed,stage",
    [
        ("init", "COMの初期化"),
        ("activate", "PowerPointの起動"),
        ("security_read", "設定取得"),
        ("security_set", "安全設定"),
        ("alerts_read", "設定取得"),
        ("alerts_set", "安全設定"),
        ("presentations", "資料一覧"),
        ("open", "読み取り専用"),
        ("fullname", "一時資料の確認"),
        ("size", "スライド寸法"),
        ("count", "スライド数"),
        ("item", "スライド1の取得"),
        ("export", "PNG出力"),
        ("empty", "スライド数"),
        ("missing_image", "出力画像"),
        ("wrong_size", "出力画像"),
    ],
)
def test_operation_errors_restore_settings_and_release_com(tmp_path, failed, stage):
    calls, state, dependencies = office(failures=[failed])
    with pytest.raises(RenderError, match=stage):
        _render_powerpoint(
            tmp_path / "private.pptx", tmp_path / "out", 160, **dependencies
        )
    assert state["security"] == 2 and state["alerts"] == 2
    assert not any(c.startswith("FORBIDDEN") for c in calls)
    assert calls.count("uninit") == (0 if failed == "init" else 1)
    if "close" in calls:
        assert state["source"] is not None


def test_unknown_document_is_not_closed(tmp_path):
    calls, state, dependencies = office(failures=["wrong_document"])
    with pytest.raises(RenderError, match="別の資料"):
        _render_powerpoint(
            tmp_path / "private.pptx", tmp_path / "out", 160, **dependencies
        )
    assert "close" not in calls and not state["user_closed"]
    assert state["security"] == 2 and state["alerts"] == 2


@pytest.mark.parametrize(
    "failed", ["close", "security_restore", "alerts_restore", "uninit"]
)
def test_cleanup_errors_are_reported_not_silently_ignored(tmp_path, failed):
    calls, state, dependencies = office(failures=[failed])
    with pytest.raises(RenderError, match="後処理|設定復元"):
        _render_powerpoint(
            tmp_path / "private.pptx", tmp_path / "out", 160, **dependencies
        )
    assert "uninit" in calls
    assert not any(c.startswith("FORBIDDEN") for c in calls)


def test_original_failure_is_preserved_when_cleanup_also_fails(tmp_path):
    calls, state, dependencies = office(failures=["export", "close"])
    with pytest.raises(RenderError) as caught:
        _render_powerpoint(
            tmp_path / "private.pptx", tmp_path / "out", 160, **dependencies
        )
    text = str(caught.value)
    assert "PNG出力" in text and "export failed" in text
    assert "後処理" in text and "close failed" in text
    assert "HRESULT 0x80020003" in text
    assert state["security"] == 2 and state["alerts"] == 2
    assert calls[-1] == "uninit"


@pytest.mark.parametrize("code", [0x80010001, 0x8001010A])
def test_only_transient_rpc_rejections_are_retried(code):
    attempts = []
    sleeps = []
    pumps = []

    def action():
        attempts.append(1)
        if len(attempts) < 4:
            raise OfficeFailure(code, "busy")
        return "ready"

    assert (
        _retry_com(
            action,
            SimpleNamespace(PumpWaitingMessages=lambda: pumps.append(1)),
            sleep=sleeps.append,
        )
        == "ready"
    )
    assert len(attempts) == 4 and len(pumps) == 3
    assert sleeps == [0.2, 0.4, 0.8]


def test_permanent_member_errors_are_not_retried():
    attempts = []

    def action():
        attempts.append(1)
        raise OfficeFailure()

    with pytest.raises(OfficeFailure):
        _retry_com(action, None, sleep=lambda _: None)
    assert len(attempts) == 1


def test_busy_retry_budget_is_bounded():
    attempts = []

    def action():
        attempts.append(1)
        raise OfficeFailure(0x80010001, "busy")

    with pytest.raises(OfficeFailure):
        _retry_com(
            action,
            SimpleNamespace(PumpWaitingMessages=lambda: None),
            sleep=lambda _: None,
        )
    assert len(attempts) == 4


def test_diagnostic_write_failure_cannot_prevent_security_restoration(tmp_path):
    calls, state, dependencies = office(failures=["open"])

    def broken_progress(label):
        raise OSError("disk full")

    with pytest.raises(RenderError, match="読み取り専用"):
        _render_powerpoint(
            tmp_path / "private.pptx",
            tmp_path / "out",
            160,
            stage_callback=broken_progress,
            **dependencies,
        )
    assert state["security"] == 2 and state["alerts"] == 2
    assert calls[-1] == "uninit"
