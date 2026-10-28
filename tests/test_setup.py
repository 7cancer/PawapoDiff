import hashlib
from zipfile import ZipFile, ZipInfo

import pytest

from scripts.create_payload import create_payload
from scripts.setup_launcher import install, SetupError


def archive(tmp_path, entries):
    path = tmp_path / "payload.zip"
    with ZipFile(path, "w") as z:
        for name, content in entries.items():
            # Writing a string filename normalizes backslashes on Windows.
            # Preserve the raw name so malformed fixtures are identical on all OSes.
            info = ZipInfo(name)
            info.filename = name
            z.writestr(info, content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_install_is_complete_offline_and_repeatable(tmp_path):
    source = tmp_path / "build"
    (source / "_internal").mkdir(parents=True)
    (source / "PawapoDiff.exe").write_bytes(b"test-executable")
    (source / "_internal/python312.dll").write_bytes(b"test-runtime")
    create_payload(source, tmp_path / "bundle")
    import json

    manifest = json.loads((tmp_path / "bundle/payload.json").read_text())
    root = tmp_path / "user-local"
    exe = install(tmp_path / "bundle/payload.zip", manifest["sha256"], root)
    assert exe.read_bytes() == b"test-executable"
    assert (exe.parent / "_internal/python312.dll").read_bytes() == b"test-runtime"
    assert install(tmp_path / "bundle/payload.zip", manifest["sha256"], root) == exe
    assert not list((root / "versions").glob(".install-*"))


def test_failed_verification_does_not_install(tmp_path):
    payload, _ = archive(tmp_path, {"PawapoDiff.exe": "app"})
    with pytest.raises(SetupError, match="checksum"):
        install(payload, "0" * 64, tmp_path / "local")
    assert not (tmp_path / "local").exists()


@pytest.mark.parametrize(
    "bad_path", ["../escape", "/absolute", "C:/outside", "a\\outside"]
)
def test_path_traversal_is_rejected(tmp_path, bad_path):
    payload, digest = archive(tmp_path, {"PawapoDiff.exe": "app", bad_path: "bad"})
    with ZipFile(payload) as z:
        assert z.infolist()[-1].orig_filename == bad_path
    with pytest.raises(SetupError, match="Unsafe"):
        install(payload, digest, tmp_path / "local")
    assert not list((tmp_path / "local/versions").iterdir())


def test_missing_executable_and_incomplete_install_are_reported(tmp_path):
    payload, digest = archive(tmp_path, {"readme": "not an app"})
    with pytest.raises(SetupError, match="missing"):
        install(payload, digest, tmp_path / "local")
    assert not list((tmp_path / "local/versions").iterdir())
    payload, digest = archive(tmp_path, {"PawapoDiff.exe": "app"})
    (tmp_path / "local/versions" / digest).mkdir()
    recovered = install(payload, digest, tmp_path / "local")
    assert recovered.parent.name == digest + "-user"
    assert recovered.read_text() == "app"
    assert (tmp_path / "local/versions" / digest).is_dir()
    assert not list((tmp_path / "local/versions" / digest).iterdir())


def test_windows_filename_normalization_cannot_hide_backslashes(tmp_path, monkeypatch):
    payload, digest = archive(tmp_path, {"PawapoDiff.exe": "app", "a\\outside": "bad"})
    original_init = ZipInfo.__init__

    def windows_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.filename = self.filename.replace("\\", "/")

    # Reproduce the Windows reader behavior even when this test runs on Linux.
    monkeypatch.setattr(ZipInfo, "__init__", windows_init)
    with ZipFile(payload) as z:
        assert z.infolist()[-1].filename == "a/outside"
        assert z.infolist()[-1].orig_filename == "a\\outside"
    with pytest.raises(SetupError, match="Unsafe"):
        install(payload, digest, tmp_path / "local")
    assert not list((tmp_path / "local/versions").iterdir())


def test_unreadable_cached_installation_is_preserved_and_reinstalled(
    tmp_path, monkeypatch
):
    from pathlib import Path

    payload, digest = archive(tmp_path, {"PawapoDiff.exe": "app"})
    first = install(payload, digest, tmp_path / "local")
    read = Path.read_text

    def denied(path, *args, **kwargs):
        if path == first.parent / ".complete":
            raise PermissionError(13, "Access denied", str(path))
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    second = install(payload, digest, tmp_path / "local")
    assert second != first and second.parent.name == digest + "-user"
    assert first.read_text() == second.read_text() == "app"
    assert install(payload, digest, tmp_path / "local") == second


def test_staging_inherits_permissions_instead_of_private_mkdtemp(tmp_path, monkeypatch):
    import scripts.setup_launcher as setup
    from pathlib import Path

    payload, digest = archive(tmp_path, {"PawapoDiff.exe": "app"})
    make = Path.mkdir
    modes = []

    def mkdir(path, mode=0o777, **kwargs):
        if path.name.startswith(".install-"):
            modes.append(mode)
        return make(path, mode=mode, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    monkeypatch.setattr(
        setup.tempfile,
        "mkdtemp",
        lambda *args, **kwargs: pytest.fail(
            "Protected OWNER RIGHTS staging is not suitable for installation"
        ),
    )
    assert install(payload, digest, tmp_path / "local").is_file()
    assert modes == [0o777]


def test_launch_access_denial_retries_only_the_same_executable(tmp_path):
    from scripts.setup_launcher import launch_application

    attempts, delays = [], []
    executable = tmp_path / "PawapoDiff.exe"

    def denied(command, **kwargs):
        attempts.append(command)
        raise PermissionError(13, "Access denied")

    with pytest.raises(PermissionError):
        launch_application(executable, popen=denied, sleep=delays.append)
    assert attempts == [[str(executable)]] * 4
    assert delays == [0.5, 1, 1.5]


def test_child_environment_keeps_non_elevating_compatibility_options(monkeypatch):
    from scripts.setup_launcher import application_environment

    monkeypatch.setenv("__COMPAT_LAYER", "WIN8RTM RunAsAdmin RunAsHighest")
    environment = application_environment()
    assert environment["__COMPAT_LAYER"] == "WIN8RTM"
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert "RunAsAdmin" in __import__("os").environ["__COMPAT_LAYER"]


def test_access_failure_message_has_stage_path_and_log(tmp_path):
    import scripts.setup_launcher as setup

    setup.phase("本体EXEの実行", tmp_path / "PawapoDiff.exe")
    text = setup.failure_message(
        PermissionError(13, "Access denied"), tmp_path / "setup.log"
    )
    assert "本体EXEの実行" in text and "PawapoDiff.exe" in text
    assert "setup.log" in text and "通常権限" in text
