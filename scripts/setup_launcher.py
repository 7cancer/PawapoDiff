"""Offline per-user installer. Frozen with payload.zip and payload.json by PyInstaller."""

import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile


class SetupError(RuntimeError):
    pass


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(payload, expected_digest, base):
    """Verify before extracting; publish a complete installation atomically."""
    if len(expected_digest) != 64 or any(
        c not in "0123456789abcdef" for c in expected_digest
    ):
        raise SetupError("Invalid installer manifest.")
    if checksum(payload) != expected_digest:
        raise SetupError(
            "Installer payload checksum mismatch. Download the installer again."
        )
    base = Path(base)
    versions = base / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    destination = versions / expected_digest
    marker = destination / ".complete"
    executable = destination / "PawapoDiff.exe"
    if (
        marker.is_file()
        and marker.read_text() == expected_digest
        and executable.is_file()
    ):
        return executable
    if destination.exists():
        # Do not overwrite an unexpected directory, or a process already using it.
        raise SetupError(
            "An incomplete installation exists. Close PawapoDiff and remove: "
            + str(destination)
        )
    staging = Path(tempfile.mkdtemp(prefix=".install-", dir=versions))
    try:
        with ZipFile(payload) as archive:
            for entry in archive.infolist():
                # ZipInfo.filename normalizes backslashes on Windows. Validate
                # the original archive name before that platform-specific rewrite.
                raw_name = entry.orig_filename
                name = PurePosixPath(raw_name)
                if (
                    name.is_absolute()
                    or ".." in name.parts
                    or "\\" in raw_name
                    or ":" in raw_name
                    or "\x00" in raw_name
                    or not name.parts
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    raise SetupError("Unsafe path in installer payload.")
                resolved = (staging / entry.filename).resolve()
                if not resolved.is_relative_to(staging.resolve()):
                    raise SetupError("Unsafe path in installer payload.")
            archive.extractall(staging)
        if not (staging / "PawapoDiff.exe").is_file():
            raise SetupError("Application executable is missing from the installer.")
        (staging / ".complete").write_text(expected_digest)
        os.replace(staging, destination)
        return executable
    finally:
        if staging.exists():
            shutil.rmtree(staging)


@contextmanager
def installation_lock(base):
    import msvcrt

    base.mkdir(parents=True, exist_ok=True)
    with (base / "setup.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise SetupError(
                "Another PawapoDiff setup is running. Wait and try again."
            ) from e
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def create_shortcut(executable, appdata):
    shortcut = Path(appdata) / "Microsoft/Windows/Start Menu/Programs/PawapoDiff.lnk"
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    quote = lambda text: "'" + str(text).replace("'", "''") + "'"
    script = (
        "$ErrorActionPreference='Stop'; "
        "$shell=New-Object -ComObject WScript.Shell; "
        f"$link=$shell.CreateShortcut({quote(shortcut)}); "
        f"$link.TargetPath={quote(executable)}; "
        f"$link.WorkingDirectory={quote(executable.parent)}; "
        "$link.Description='PawapoDiff'; $link.Save()"
    )
    import base64

    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--install-only",
        action="store_true",
        help="Install without opening the app (build verification).",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SetupError("PawapoDiff Setup requires Windows 10/11.")
    local = os.environ.get("LOCALAPPDATA")
    roaming = os.environ.get("APPDATA")
    if not local:
        raise SetupError("LOCALAPPDATA is unavailable.")
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    manifest = json.loads((bundle / "payload.json").read_text(encoding="utf-8"))
    base = Path(local) / "PawapoDiff"
    print("PawapoDiff: Preparing the application and bundled Python environment...")
    print(
        "No administrator access, Python, Node.js, or Internet connection is required."
    )
    print("Microsoft PowerPoint must already be installed and activated.")
    with installation_lock(base):
        executable = install(bundle / "payload.zip", manifest["sha256"], base)
        if roaming:
            try:
                create_shortcut(executable, roaming)
            except (OSError, subprocess.SubprocessError) as error:
                print("Could not create the Start Menu shortcut:", error)
        if args.install_only:
            print("Installed:", executable)
            return
        subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
        )
    print("PawapoDiff started. Use the Start Menu shortcut next time.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = "PawapoDiff setup failed:\n" + str(error)
        print(message)
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, message, "PawapoDiff Setup", 0x10)
        raise SystemExit(1)
