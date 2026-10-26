"""Exercise real Windows IDispatch, with only the documented PowerPoint members.

No fake HWND is fabricated: the COM server has no such member. These tests run
without Office, and are intentionally distinct from actual PowerPoint validation.
"""

import sys
from pathlib import Path

import pytest
from PIL import Image
from pawapodiff.render import _render_powerpoint, RenderError

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Requires actual Windows COM dispatch"
)


@pytest.mark.parametrize("failure", [None, "open", "export", "close"])
def test_real_idispatch_document_contract(tmp_path, failure):
    import pythoncom
    import win32com.client
    from win32com.server.util import wrap
    from win32com.server.exception import COMException

    state = {
        "security": 2,
        "alerts": 2,
        "closed": 0,
        "quit": 0,
        "source": None,
        "user_document_closed": False,
    }

    def fail(label):
        if failure == label:
            raise COMException(desc=label + " failed", scode=-2147024809)

    class Setup:
        _public_methods_ = []  # Required even for property-only pywin32 COM servers.
        _public_attrs_ = ["SlideWidth", "SlideHeight"]
        SlideWidth = 960
        SlideHeight = 540

    class Slide:
        _public_methods_ = ["Export"]

        def Export(self, path, kind, width, height):
            fail("export")
            assert state["security"] == 2 and state["alerts"] == 2
            Image.new("RGB", (width, height)).save(path)

    class Slides:
        _public_attrs_ = ["Count"]
        _public_methods_ = ["Item"]
        Count = 3

        def Item(self, index):
            return wrap(Slide())

    class Presentation:
        _public_attrs_ = ["FullName", "PageSetup", "Slides"]
        _public_methods_ = ["Close"]

        @property
        def FullName(self):
            return state["source"]

        @property
        def PageSetup(self):
            return wrap(Setup())

        @property
        def Slides(self):
            return wrap(Slides())

        def Close(self):
            state["closed"] += 1
            fail("close")

    class Presentations:
        _public_methods_ = ["Open"]

        def Open(self, path, readonly, untitled, with_window):
            assert (readonly, untitled, with_window) == (-1, 0, 0)
            assert state["security"] == 3 and state["alerts"] == 1
            fail("open")
            state["source"] = path
            return wrap(Presentation())

    class Application:
        # Deliberately no HWND or Visible member.
        _public_attrs_ = ["AutomationSecurity", "DisplayAlerts", "Presentations"]
        _public_methods_ = ["Quit"]

        @property
        def AutomationSecurity(self):
            return state["security"]

        @AutomationSecurity.setter
        def AutomationSecurity(self, value):
            state["security"] = value

        @property
        def DisplayAlerts(self):
            return state["alerts"]

        @DisplayAlerts.setter
        def DisplayAlerts(self, value):
            state["alerts"] = value

        @property
        def Presentations(self):
            return wrap(Presentations())

        def Quit(self):
            state["quit"] += 1
            raise COMException(
                desc="Must never quit a shared Office application", scode=-2147024809
            )

    pythoncom.CoInitialize()
    app = client = None
    try:
        app = win32com.client.Dispatch(wrap(Application()))

        class Client:
            def DispatchEx(self, name):
                return app

        client = Client()
        kwargs = dict(com=pythoncom, client=client, sleep=lambda _: None)
        if failure:
            with pytest.raises(RenderError, match="failed"):
                _render_powerpoint(
                    tmp_path / "private.pptx", tmp_path / "out", 160, **kwargs
                )
        else:
            paths = _render_powerpoint(
                tmp_path / "private.pptx", tmp_path / "out", 160, **kwargs
            )
            assert len(paths) == 3
        assert state["security"] == 2 and state["alerts"] == 2
        assert state["closed"] == (0 if failure == "open" else 1)
        assert state["quit"] == 0 and not state["user_document_closed"]
    finally:
        app = client = None
        pythoncom.CoUninitialize()
