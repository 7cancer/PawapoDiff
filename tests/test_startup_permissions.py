"""Never start the interactive app or actual Office worker elevated."""

import sys
import pytest

from pawapodiff import windows_runtime


def test_redirected_startup_messages_are_utf8_even_on_ansi_windows(monkeypatch):
    import io

    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    windows_runtime.configure_console()
    print("アクセスが拒否されました。通常権限で起動してください。")
    stream.flush()
    assert "通常権限" in output.getvalue().decode("utf-8")


def test_standard_user_guard_does_not_start_an_elevated_app(monkeypatch):
    import pawapodiff.__main__ as application

    monkeypatch.setattr(sys, "argv", ["PawapoDiff"])
    monkeypatch.setattr(windows_runtime, "is_elevated", lambda: True)
    messages = []
    monkeypatch.setattr(windows_runtime, "show_startup_error", messages.append)
    monkeypatch.setattr(
        application,
        "create_app",
        lambda *args, **kwargs: pytest.fail(
            "An elevated interactive server must not start"
        ),
    )
    assert application.main() == 1
    assert "通常権限" in messages[0]


def test_elevated_setup_stops_before_installing_or_launching(monkeypatch):
    import scripts.setup_launcher as setup

    monkeypatch.setattr(sys, "argv", ["PawapoDiff-Setup.exe"])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows_runtime, "is_elevated", lambda: True)
    monkeypatch.setattr(
        setup,
        "install",
        lambda *args: pytest.fail("Interactive elevated setup must not install"),
    )
    with pytest.raises(windows_runtime.ElevatedLaunchError, match="通常権限"):
        setup.main()


def test_standard_guard_allows_normal_context(monkeypatch):
    monkeypatch.setattr(windows_runtime, "is_elevated", lambda: False)
    windows_runtime.require_standard_user()


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows ACL/token APIs")
def test_installed_tree_is_readable_using_a_non_admin_token(tmp_path):
    import os
    import secrets
    from pathlib import Path
    import shutil
    import win32security
    from scripts.windows_standard_user import (
        standard_user_token,
        grant_test_user_access,
    )
    from scripts.create_payload import create_payload
    from scripts.setup_launcher import install
    import json

    if not windows_runtime.is_elevated():
        pytest.skip(
            "Elevated fixture owner is required; CI exercises the filtered user"
        )
    root = Path(os.environ["LOCALAPPDATA"]) / (
        "PawapoDiff-ACL-test-" + secrets.token_hex(8)
    )
    root.mkdir()
    grant_test_user_access(root)
    try:
        source = root / "source"
        source.mkdir()
        (source / "PawapoDiff.exe").write_bytes(b"executable")
        (source / "_internal").mkdir()
        (source / "_internal/python312.dll").write_bytes(b"runtime")
        create_payload(source, root / "bundle")
        digest = json.loads((root / "bundle/payload.json").read_text())["sha256"]
        executable = install(root / "bundle/payload.zip", digest, root / "local")
        with standard_user_token() as token:
            win32security.ImpersonateLoggedOnUser(token)
            try:
                assert executable.read_bytes() == b"executable"
                assert (
                    executable.parent / "_internal/python312.dll"
                ).read_bytes() == b"runtime"
                assert (executable.parent / ".complete").read_text() == digest
                assert (
                    install(root / "bundle/payload.zip", digest, root / "local")
                    == executable
                )
            finally:
                win32security.RevertToSelf()
    finally:
        shutil.rmtree(root, ignore_errors=True)
