import hashlib
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.create_payload import create_payload
from scripts.setup_launcher import install, SetupError


def archive(tmp_path, entries):
    path = tmp_path / "payload.zip"
    with ZipFile(path, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
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
    with pytest.raises(SetupError, match="incomplete"):
        install(payload, digest, tmp_path / "local")
