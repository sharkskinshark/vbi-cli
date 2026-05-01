"""Small terminal helpers shared by resident views."""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager


def wait_for_exit(seconds: float) -> bool:
    """Wait up to ``seconds``; return True when Ctrl+C or ``q`` requests exit."""
    deadline = time.monotonic() + max(0, seconds)
    with _ctrl_c_as_keypress():
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                time.sleep(min(0.1, remaining))
            except KeyboardInterrupt:
                return True
            if _exit_key_pressed():
                return True


@contextmanager
def _ctrl_c_as_keypress():
    """On Windows, make Ctrl+C readable as ``\\x03`` during resident views."""
    if os.name != "nt" or not sys.stdin.isatty():
        yield
        return

    try:
        import ctypes
    except ImportError:
        yield
        return

    kernel32 = ctypes.windll.kernel32
    std_input_handle = -10
    enable_processed_input = 0x0001
    enable_line_input = 0x0002
    enable_echo_input = 0x0004

    handle = kernel32.GetStdHandle(std_input_handle)
    if handle == ctypes.c_void_p(-1).value:
        yield
        return

    original_mode = ctypes.c_uint()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(original_mode)):
        yield
        return

    raw_mode = original_mode.value & ~(
        enable_processed_input | enable_line_input | enable_echo_input
    )
    changed = bool(kernel32.SetConsoleMode(handle, raw_mode))
    try:
        yield
    finally:
        if changed:
            kernel32.SetConsoleMode(handle, original_mode.value)


def _exit_key_pressed() -> bool:
    if os.name != "nt" or not sys.stdin.isatty():
        return False
    try:
        import msvcrt
    except ImportError:
        return False

    while msvcrt.kbhit():
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            # Function/arrow key prefix; consume the payload and ignore it.
            if msvcrt.kbhit():
                msvcrt.getwch()
            continue
        if ch in ("\x03", "q", "Q"):
            return True
    return False
