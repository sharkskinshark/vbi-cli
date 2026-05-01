"""Small terminal helpers shared by resident views."""

from __future__ import annotations

import os
import sys
import time


def wait_for_exit(seconds: float) -> bool:
    """Wait up to ``seconds``; return True when Ctrl+C or ``q`` requests exit."""
    deadline = time.monotonic() + max(0, seconds)
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
