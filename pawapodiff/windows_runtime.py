"""Windows startup context; no privilege escalation or Office process changes."""

import ctypes
import os
import sys
from contextlib import contextmanager

STANDARD_USER_MESSAGE = (
    "PawapoDiffは通常権限で起動してください。\n"
    "管理者権限でのPowerPoint連携には対応していません。\n"
    "PawapoDiffを終了し、「管理者として実行」を使わずに起動してください。\n"
    "EXEやショートカットの互換性設定で「管理者としてこのプログラムを実行する」が有効な場合は解除してください。\n"
    "PowerPointも通常権限で起動してください。"
)


class ElevatedLaunchError(RuntimeError):
    pass


def is_elevated():
    if os.name != "nt":
        return False
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.IsUserAnAdmin.argtypes = []
    shell.IsUserAnAdmin.restype = ctypes.c_int
    return bool(shell.IsUserAnAdmin())


def require_standard_user():
    if is_elevated():
        raise ElevatedLaunchError(STANDARD_USER_MESSAGE)


def show_startup_error(message, title="PawapoDiff"):
    print(message)
    if os.name == "nt":
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MessageBoxW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.UINT,
        ]
        user.MessageBoxW.restype = ctypes.c_int
        user.MessageBoxW(None, message, title, 0x10)


@contextmanager
def child_dll_search():
    """Do not pass the setup bootloader's temporary DLL directory to children."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    size = kernel.GetDllDirectoryW(0, None)
    buffer = ctypes.create_unicode_buffer(size + 1)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.SetDllDirectoryW(buffer.value or None):
            raise ctypes.WinError(ctypes.get_last_error())
