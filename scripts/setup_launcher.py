"""Offline per-user installer. Frozen with payload.zip and payload.json by PyInstaller."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import secrets
import time
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile
from pawapodiff.windows_runtime import (
    require_standard_user,
    show_startup_error,
    child_dll_search,
)


logger = logging.getLogger("PawapoDiff.Setup")
PHASE = "セットアップの起動"
TARGET = None


def phase(label, target=None):
    global PHASE, TARGET
    PHASE, TARGET = label, str(target) if target is not None else None
    logger.info("stage=%s target=%s", PHASE, TARGET)


def configure_log():
    directories = []
    if os.environ.get("LOCALAPPDATA"):
        directories.append(
            Path(os.environ["LOCALAPPDATA"]) / "PawapoDiff" / "setup-logs"
        )
    directories.append(Path(tempfile.gettempdir()))
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"PawapoDiff-Setup-{os.getpid()}.log"
            handler = logging.FileHandler(path, encoding="utf-8")
        except OSError:
            continue
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        from pawapodiff import __version__

        logger.info("PawapoDiff setup version=%s", __version__)
        return path
    return None


def failure_message(error, log_path):
    message = f"PawapoDiffを起動できませんでした。\n処理段階: {PHASE}\n対象: {TARGET or '不明'}\n{type(error).__name__}: {error}"
    if isinstance(error, PermissionError):
        message += "\nアクセス権またはWindowsの実行制限で拒否されました。管理者として実行せず、通常権限で実行してください。\n本体EXEの実行が拒否される場合は、表示されたパスについて会社の管理者に実行許可を確認してください。"
    message += f"\n起動ログ: {log_path or '保存先を作成できませんでした'}"
    return message


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
    with ZipFile(payload) as archive:
        entries = archive.infolist()
        for entry in entries:
            # Validate the original name before ZipInfo's Windows normalization.
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
        critical = [
            entry.filename
            for entry in entries
            if entry.filename.endswith(
                ("python312.dll", "base_library.zip", "frontend/dist/index.html")
            )
        ]

    # Opening a marker can itself raise WinError 5 for an older installation
    # created with a protected owner-only ACL by an elevated installer.
    def reusable(destination):
        try:
            marker = destination / ".complete"
            if not marker.is_file() or marker.read_text() != expected_digest:
                return False
            paths = [destination / "PawapoDiff.exe"]
            paths += [destination / name for name in critical]
            for path in paths:
                with path.open("rb") as stream:
                    stream.read(1)
            return True
        except (OSError, UnicodeError):
            logger.warning(
                "Existing installation cannot be reused: %s", destination, exc_info=True
            )
            return False

    for suffix in ("", "-user", "-user-" + secrets.token_hex(6)):
        destination = versions / (expected_digest + suffix)
        phase("導入先の確認", destination)
        if reusable(destination):
            return destination / "PawapoDiff.exe"
        try:
            exists = destination.exists()
        except PermissionError:
            exists = True
        if not exists:
            break
        logger.warning(
            "Keeping unreadable/incomplete installation; selecting a fresh directory: %s",
            destination,
        )
    else:
        raise SetupError(
            "読み取り可能な新しい導入先を作成できません。起動ログを確認してください。"
        )
    executable = destination / "PawapoDiff.exe"
    # Do not use mkdtemp(mode=0700): on Windows its protected OWNER RIGHTS
    # ACL can make files installed by an elevated administrator unreadable to
    # the same account's normal token. Inherit the user's profile permissions.
    for _ in range(100):
        staging = versions / (".install-" + secrets.token_hex(12))
        try:
            staging.mkdir(mode=0o777)
            break
        except FileExistsError:
            continue
    else:
        raise SetupError("一時導入先を作成できません。")
    phase("実行環境の展開", staging)
    try:
        with ZipFile(payload) as archive:
            for entry in entries:
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
        try:
            if staging.exists():
                shutil.rmtree(staging)
        except OSError:
            logger.warning(
                "Could not clean installation staging: %s", staging, exc_info=True
            )


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
    temporary = shortcut.with_name(f".PawapoDiff-{secrets.token_hex(8)}.lnk")
    quote = lambda text: "'" + str(text).replace("'", "''") + "'"
    script = (
        "$ErrorActionPreference='Stop'; "
        "$shell=New-Object -ComObject WScript.Shell; "
        f"$link=$shell.CreateShortcut({quote(temporary)}); "
        f"$link.TargetPath={quote(executable)}; "
        f"$link.WorkingDirectory={quote(executable.parent)}; "
        "$link.Description='PawapoDiff'; $link.Save()"
    )
    import base64

    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        with child_dll_search():
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                check=True,
                timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=application_environment(),
            )
        # A fresh link clears a previously set "Run as administrator" flag.
        # Replace atomically rather than loading/updating the old link's flags.
        os.replace(temporary, shortcut)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Could not remove temporary shortcut: %s", temporary, exc_info=True
            )


@contextmanager
def accessible_installation(local):
    # Only installation-directory/lock access triggers this fallback. An EXE
    # execution denial must not be worked around by rotating executable paths.
    for name in ("PawapoDiff", "PawapoDiff-User"):
        base = Path(local) / name
        phase("ユーザー用の導入先を準備", base)
        lock = installation_lock(base)
        try:
            lock.__enter__()
        except PermissionError:
            logger.warning("Installation root is inaccessible: %s", base, exc_info=True)
            continue
        try:
            yield base
        finally:
            lock.__exit__(None, None, None)
        return
    raise SetupError(
        "ユーザー用の導入先にアクセスできません。管理者権限ではなくフォルダーのアクセス権を確認してください。"
    )


def application_environment():
    # Compatibility overrides inherited from an elevated parent must not be
    # propagated to the installed app or its runtime verification process.
    environment = dict(os.environ)
    for key in list(environment):
        if key.upper() == "__COMPAT_LAYER":
            layers = [
                value
                for value in environment[key].split()
                if value.casefold() not in {"runasadmin", "runashighest"}
            ]
            if layers:
                environment[key] = " ".join(layers)
            else:
                del environment[key]
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def verify_runtime(executable):
    phase("同梱ランタイムの起動確認", executable)
    with child_dll_search():
        result = subprocess.run(
            [str(executable), "--check-runtime"],
            cwd=executable.parent,
            env=application_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    logger.info(
        "Runtime check exit=%s stdout=%s stderr=%s",
        result.returncode,
        result.stdout[-8000:],
        result.stderr[-8000:],
    )
    if result.returncode:
        raise SetupError(
            "同梱ランタイムを起動できませんでした。\n"
            + (result.stderr or result.stdout)[-3000:]
        )


def launch_application(executable, *, popen=subprocess.Popen, sleep=time.sleep):
    phase("本体EXEの実行", executable)
    # A scanner may briefly lock a freshly extracted EXE. Retry the same file
    # only; do not disable security software or try alternate paths on denial.
    for attempt in range(4):
        try:
            with child_dll_search():
                return popen(
                    [str(executable)],
                    cwd=executable.parent,
                    creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
                    env=application_environment(),
                )
        except PermissionError:
            if attempt == 3:
                raise
            logger.warning(
                "Application execution denied; retrying the same path", exc_info=True
            )
            sleep(0.5 * (attempt + 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--install-only",
        action="store_true",
        help="Install and verify without opening the app (build verification).",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SetupError("PawapoDiff Setup requires Windows 10/11.")
    # Install-only is also used by the elevated Windows build service. It never
    # opens PowerPoint or starts an interactive elevated app.
    if not args.install_only:
        phase("起動権限の確認")
        require_standard_user()
    local, roaming = os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")
    if not local:
        raise SetupError("LOCALAPPDATA is unavailable.")
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    phase("同梱ファイルの確認", bundle)
    manifest = json.loads((bundle / "payload.json").read_text(encoding="utf-8"))
    print(
        "PawapoDiff: ユーザー用の実行環境を準備します。管理者権限・Python・Node.jsの導入は不要です。"
    )
    with accessible_installation(local) as base:
        executable = install(bundle / "payload.zip", manifest["sha256"], base)
        verify_runtime(executable)
        state = {"executable": str(executable), "sha256": manifest["sha256"]}
        try:
            temporary = base / "install-state.new"
            temporary.write_text(json.dumps(state), encoding="utf-8")
            os.replace(temporary, base / "install-state.json")
        except OSError:
            logger.warning("Could not save installation state", exc_info=True)
        if roaming:
            phase("スタートメニューの作成", executable)
            try:
                create_shortcut(executable, roaming)
            except (OSError, subprocess.SubprocessError):
                logger.warning("Could not create shortcut", exc_info=True)
        if args.install_only:
            print("Installed:", executable)
            return
        launch_application(executable)
    print(
        "PawapoDiffを通常権限で起動しました。次回からスタートメニューを利用できます。"
    )


if __name__ == "__main__":
    log_path = configure_log()
    try:
        main()
    except Exception as error:
        logger.exception("Setup failed: stage=%s target=%s", PHASE, TARGET)
        show_startup_error(failure_message(error, log_path), "PawapoDiff Setup")
        raise SystemExit(1)
