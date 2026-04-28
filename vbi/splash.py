"""Startup splash + animated provider sync.

Shown once on `vbi live` boot. Big chunky-block banner with a horizontal
gradient, then a per-adapter sync checklist. Subsequent refreshes go
straight to the dashboard renderer with no splash.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

try:
    from pyfiglet import Figlet
    _HAS_FIGLET = True
except ImportError:  # graceful fallback if pyfiglet not installed
    _HAS_FIGLET = False

from .contracts import NormalizedRecord
from .registry import get_adapters


# ── colors ───────────────────────────────────────────────────────────────────
def _c(code: str) -> str:
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m"
    return ""


_AMBER = _c("38;5;208")
_DIM   = _c("2")
_BOLD  = _c("1")
_GREEN = _c("32")
_YLW   = _c("93")
_RED   = _c("91")
_RST   = _c("0")


# ── banner ───────────────────────────────────────────────────────────────────
_BANNER_FONT  = "ansi_shadow"
# Warm horizontal gradient: orange → gold (matches Claude-Code-style amber)
_GRADIENT_L   = (255, 120, 40)
_GRADIENT_R   = (255, 215, 130)
_TAGLINE      = "    Local-first AI usage inspection"
_BYLINE       = "      CLUSTER&Associates  Architecture Design"
_FULLNAME     = "         Visual Budget Inspection"
_RELEASE_DATE = "2026-04-28"


def _version() -> str:
    """Read package version from installed metadata; fall back when run from source."""
    try:
        from importlib.metadata import version
        return version("vbi-cli")
    except Exception:  # noqa: BLE001
        # Fallback: parse pyproject.toml if available
        try:
            import tomllib
            from pathlib import Path
            p = Path(__file__).resolve().parent.parent / "pyproject.toml"
            if p.is_file():
                with p.open("rb") as f:
                    return tomllib.load(f).get("project", {}).get("version", "0.0.0")
        except Exception:  # noqa: BLE001
            pass
    return "0.0.0"


def _gradient_line(line: str, max_w: int, l_rgb: tuple[int, int, int],
                   r_rgb: tuple[int, int, int]) -> str:
    """Apply horizontal RGB gradient across one line. Skips coloring on spaces."""
    out: list[str] = []
    last_color = ""
    for i, ch in enumerate(line):
        if ch == " ":
            out.append(ch)
            continue
        ratio = i / max(1, max_w - 1)
        r = int(l_rgb[0] + (r_rgb[0] - l_rgb[0]) * ratio)
        g = int(l_rgb[1] + (r_rgb[1] - l_rgb[1]) * ratio)
        b = int(l_rgb[2] + (r_rgb[2] - l_rgb[2]) * ratio)
        color = f"\033[38;2;{r};{g};{b}m"
        if color != last_color:
            out.append(color)
            last_color = color
        out.append(ch)
    out.append("\033[0m")
    return "".join(out)


def _print_banner() -> None:
    is_tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    if _HAS_FIGLET:
        banner = Figlet(font=_BANNER_FONT, width=100).renderText("VBI CLI")
    else:
        # Fallback for environments without pyfiglet
        banner = "\n  V B I   C L I\n"

    lines = [l.rstrip() for l in banner.splitlines() if l.strip()]
    max_w = max((len(l) for l in lines), default=0)

    sys.stdout.write("\n")
    for line in lines:
        if is_tty:
            sys.stdout.write(_gradient_line(line, max_w, _GRADIENT_L, _GRADIENT_R) + "\n")
        else:
            sys.stdout.write(line + "\n")
    sys.stdout.write(f"{_DIM}{_TAGLINE}{_RST}\n")
    sys.stdout.write(f"{_DIM}\033[3m{_BYLINE}{_RST}\n")
    sys.stdout.write(f"{_DIM}\033[3m{_FULLNAME}{_RST}\n")
    sys.stdout.write(f"{_DIM}            v{_version()}  ·  {_RELEASE_DATE}{_RST}\n\n")
    sys.stdout.flush()


# ── per-adapter sync progress ────────────────────────────────────────────────
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _summary(record: NormalizedRecord | None) -> str:
    """One-line per-record summary shown after a successful sync."""
    if record is None or record.source_type == "unavailable":
        return ""
    if record.usage_value is not None and record.quota_limit:
        pct = int(round(record.usage_value / record.quota_limit * 100))
        return f"{int(record.usage_value)} / {int(record.quota_limit)}  {pct}%"
    if record.usage_value is not None:
        v = record.usage_value
        unit = record.unit or ""
        for t, sfx in ((1_000_000, "M"), (1_000, "K")):
            if abs(v) >= t:
                return f"{v / t:.1f}{sfx} {unit}".strip()
        return f"{int(v)} {unit}".strip()
    if record.session_count:
        return f"{record.session_count} sessions"
    return "ok"


def splash_sync() -> list[NormalizedRecord]:
    """Show splash, sync each adapter with live status updates, return records."""
    _print_banner()

    adapters: list[Any] = [
        a for a in get_adapters()
        if getattr(a, "adapter_tier", "live") == "live"
    ]
    if not adapters:
        return []

    is_tty = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    sys.stdout.write(f"{_DIM}  Syncing {len(adapters)} providers...{_RST}\n\n")

    if is_tty:
        for a in adapters:
            sys.stdout.write(f"  {_DIM}[ ]{_RST} {a.product}\n")
        sys.stdout.flush()

    records: list[NormalizedRecord] = []
    n = len(adapters)

    for idx, adapter in enumerate(adapters):
        if is_tty:
            lines_up = n - idx
            sys.stdout.write(f"\033[{lines_up}A\r\033[K")
            spin = _SPINNER[idx % len(_SPINNER)]
            sys.stdout.write(f"  {_YLW}[{spin}]{_RST} {adapter.product} {_DIM}syncing...{_RST}")
            sys.stdout.flush()

        record: NormalizedRecord | None = None
        ok = False
        try:
            result = adapter.sync()
            record = result.record
            ok = bool(record and record.source_type != "unavailable")
        except Exception:  # noqa: BLE001
            ok = False

        if is_tty:
            sys.stdout.write("\r\033[K")
            mark = f"{_GREEN}[✓]{_RST}" if ok else f"{_RED}[!]{_RST}"
            extra = _summary(record) if ok else "unavailable"
            sys.stdout.write(f"  {mark} {adapter.product:<14}  {_DIM}{extra}{_RST}")
            sys.stdout.write(f"\033[{lines_up}B\r")
        else:
            mark = "✓" if ok else "!"
            extra = _summary(record) if ok else "unavailable"
            sys.stdout.write(f"  [{mark}] {adapter.product:<14}  {extra}\n")
        sys.stdout.flush()

        if ok and record is not None:
            records.append(record)

    sys.stdout.write("\n")
    sys.stdout.flush()

    # Cached update-check (network at most once per 24h)
    try:
        from .update_cmd import maybe_check_cached
        count, subject = maybe_check_cached()
        if count > 0:
            hint = f"  {_YLW}↑ update available[{_RST}{_DIM} {count} commit{'s' if count != 1 else ''} behind{_RST}{_YLW}]{_RST}"
            run_hint = f"  {_DIM}run [{_RST}{_BOLD}vbi update{_RST}{_DIM}] to upgrade{_RST}"
            sys.stdout.write(f"{hint}\n{run_hint}\n\n")
            sys.stdout.flush()
    except Exception:  # noqa: BLE001 — never let update-check break the splash
        pass

    if is_tty:
        time.sleep(0.4)
    return records
