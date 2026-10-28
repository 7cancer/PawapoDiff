"""CI-only verification with a medium-integrity token and Administrators disabled.

This does not change accounts, application-control policy, or Office settings.
All ACL fixtures and executions are confined to a newly created test directory.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys


@contextmanager
def standard_user_token():
    import win32api
    import win32con
    import win32security

    original = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ALL_ACCESS
    )
    token = None
    try:
        admin = win32security.CreateWellKnownSid(
            win32security.WinBuiltinAdministratorsSid
        )
        user = win32security.GetTokenInformation(original, win32security.TokenUser)[0]
        token = win32security.CreateRestrictedToken(
            original, win32security.DISABLE_MAX_PRIVILEGE, [(admin, 0)], None, None
        )
        win32security.SetTokenInformation(token, win32security.TokenOwner, user)
        medium = win32security.ConvertStringSidToSid("S-1-16-8192")
        win32security.SetTokenInformation(
            token,
            win32security.TokenIntegrityLevel,
            (medium, win32security.SE_GROUP_INTEGRITY),
        )
        yield token
    finally:
        if token is not None:
            token.Close()
        original.Close()


def grant_test_user_access(path):
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    flags = win32con.CONTAINER_INHERIT_ACE | win32con.OBJECT_INHERIT_ACE
    for sid in (
        user,
        win32security.CreateWellKnownSid(win32security.WinLocalSystemSid),
        win32security.CreateWellKnownSid(win32security.WinBuiltinAdministratorsSid),
    ):
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION, flags, win32con.GENERIC_ALL, sid
        )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "S:(ML;OICI;NW;;;ME)", win32security.SDDL_REVISION_1
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.LABEL_SECURITY_INFORMATION,
        None,
        None,
        None,
        descriptor.GetSecurityDescriptorSacl(),
    )


def restrict_legacy_directory(path):
    import win32security

    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)", win32security.SDDL_REVISION_1
    )
    win32security.SetNamedSecurityInfo(
        str(path),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        descriptor.GetSecurityDescriptorDacl(),
        None,
    )


@contextmanager
def standard_user_desktop(token):
    """Give the filtered token its own CI desktop, without changing existing ACLs.

    Hosted runners use a service window station that can admit Administrators
    only. Removing that group otherwise causes user32 initialization to fail
    before the executable's entry point (STATUS_DLL_INIT_FAILED).
    """
    import pywintypes
    import win32con
    import win32security
    import win32service

    user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    sid = win32security.ConvertSidToStringSid(user)
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        f"D:P(A;;GA;;;{sid})(A;;GA;;;SY)(A;;GA;;;BA)S:(ML;;NW;;;ME)",
        win32security.SDDL_REVISION_1,
    )
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    name = "PawapoDiffCI-" + secrets.token_hex(8)
    station = win32service.CreateWindowStation(
        name, 0, win32con.GENERIC_ALL, attributes
    )
    desktop = None
    try:
        previous = win32service.GetProcessWindowStation()
        try:
            station.SetProcessWindowStation()
            desktop = win32service.CreateDesktop(
                "Default", 0, win32con.GENERIC_ALL, attributes
            )
        finally:
            previous.SetProcessWindowStation()
            # GetProcessWindowStation returns a borrowed handle.
            previous.Detach()
        yield name + "\\Default"
    finally:
        if desktop is not None:
            desktop.CloseDesktop()
            desktop.Detach()
        station.CloseWindowStation()
        station.Detach()


def run_standard(
    token, command, directory, environment, label, *, desktop, timeout=120
):
    import pywintypes
    import win32con
    import win32event
    import win32file
    import win32process

    output = directory / (label + ".log")
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.bInheritHandle = True
    log = win32file.CreateFile(
        str(output),
        win32con.GENERIC_WRITE,
        win32con.FILE_SHARE_READ,
        attributes,
        win32con.CREATE_ALWAYS,
        0,
        None,
    )
    stdin = win32file.CreateFile(
        "NUL",
        win32con.GENERIC_READ,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
        attributes,
        win32con.OPEN_EXISTING,
        0,
        None,
    )
    process = thread = None
    try:
        startup = win32process.STARTUPINFO()
        startup.lpDesktop = desktop
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = stdin, log, log
        process, thread, _, _ = win32process.CreateProcessAsUser(
            token,
            str(command[0]),
            subprocess.list2cmdline([str(value) for value in command]),
            None,
            None,
            True,
            win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT,
            environment,
            str(directory),
            startup,
        )
        if (
            win32event.WaitForSingleObject(process, timeout * 1000)
            == win32con.WAIT_TIMEOUT
        ):
            win32process.TerminateProcess(process, 1)
            win32event.WaitForSingleObject(process, 5000)
            raise RuntimeError(f"{label} timed out")
        code = win32process.GetExitCodeProcess(process)
    finally:
        if thread is not None:
            thread.Close()
        if process is not None:
            process.Close()
        stdin.Close()
        log.Close()
    text = output.read_text(encoding="utf-8", errors="replace")
    if code:
        raise RuntimeError(f"{label}: exit={code}\n{text[-6000:]}")
    return text


def verify(distribution, manifest):
    from scripts.setup_launcher import create_shortcut
    import win32security

    directory = Path(os.environ["LOCALAPPDATA"]) / (
        "PawapoDiff-CI-standard-" + secrets.token_hex(8)
    )
    directory.mkdir()
    grant_test_user_access(directory)
    try:
        local, roaming, temporary = [
            directory / name for name in ("Local", "Roaming", "Temp")
        ]
        for path in (local, roaming, temporary):
            path.mkdir()
        setup = directory / "PawapoDiff-Setup.exe"
        shutil.copyfile(distribution / setup.name, setup)
        digest = json.loads(manifest.read_text())["sha256"]
        base = local / "PawapoDiff"
        old = base / "versions" / digest
        old.mkdir(parents=True)
        (old / "PawapoDiff.exe").write_bytes(b"old protected installation")
        (old / ".complete").write_text(digest)
        restrict_legacy_directory(old)
        create_shortcut(old / "PawapoDiff.exe", roaming)
        shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/PawapoDiff.lnk"
        content = bytearray(shortcut.read_bytes())
        content[20:24] = (int.from_bytes(content[20:24], "little") | 0x2000).to_bytes(
            4, "little"
        )
        shortcut.write_bytes(content)
        environment = dict(
            os.environ,
            LOCALAPPDATA=str(local),
            APPDATA=str(roaming),
            TEMP=str(temporary),
            TMP=str(temporary),
            PYINSTALLER_RESET_ENVIRONMENT="1",
        )
        with standard_user_token() as token, standard_user_desktop(token) as desktop:
            # Verify the fixture really is inaccessible to the filtered token.
            win32security.ImpersonateLoggedOnUser(token)
            try:
                try:
                    (old / ".complete").read_text()
                except PermissionError:
                    pass
                else:
                    raise AssertionError(
                        "Legacy ACL fixture was readable to the standard token"
                    )
            finally:
                win32security.RevertToSelf()
            run_standard(
                token,
                [setup, "--install-only"],
                directory,
                environment,
                "standard-first-install",
                desktop=desktop,
            )
            state = json.loads((base / "install-state.json").read_text())
            installed = Path(state["executable"])
            assert installed.parent.name == digest + "-user", installed
            run_standard(
                token,
                [setup, "--install-only"],
                directory,
                environment,
                "standard-repeat-install",
                desktop=desktop,
            )
            assert json.loads((base / "install-state.json").read_text())[
                "executable"
            ] == str(installed)
            proof = run_standard(
                token,
                [installed, "--check-runtime"],
                directory,
                environment,
                "standard-installed-runtime",
                desktop=desktop,
            )
            if '"elevated": false' not in proof:
                raise AssertionError(
                    "Installed application was not verified as a standard user: "
                    + proof
                )
            portable = directory / "Portable"
            shutil.copytree(distribution / "PawapoDiff", portable)
            proof = run_standard(
                token,
                [portable / "PawapoDiff.exe", "--check-runtime"],
                directory,
                environment,
                "standard-portable-runtime",
                desktop=desktop,
            )
            if '"elevated": false' not in proof:
                raise AssertionError(
                    "Portable runtime was not verified as a standard user: " + proof
                )
        assert (old / "PawapoDiff.exe").read_bytes() == b"old protected installation"
        assert not int.from_bytes(shortcut.read_bytes()[20:24], "little") & 0x2000, (
            "Old RunAsAdmin shortcut flag survived"
        )
        print(
            "PASS: medium-integrity, Administrators-disabled install/repeat, legacy ACL recovery, fresh non-admin shortcut, installed + portable runtime and worker startup."
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit("Windows only")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pawapodiff.windows_runtime import configure_console

    configure_console()
    try:
        verify(
            Path("dist").resolve(), Path("build/setup-payload/payload.json").resolve()
        )
    except Exception as error:
        # Public annotations remain readable when full job logs require login.
        message = (
            str(error).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        )
        print("::error title=Standard-user installer verification::" + message)
        raise
