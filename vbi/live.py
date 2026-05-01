"""Real-time bar-chart terminal view.

Unlike ``vbi dashboard`` (cache-only), ``vbi live`` calls ``adapter.sync()``
on every refresh so the numbers are always current.

Each provider block shows:
  - Usage bar   : filled portion = usage consumed vs. quota
  - Time bar    : filled portion = time elapsed in current reset period
                  (empty = just reset, full = about to reset)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from .contracts import NormalizedRecord
from .registry import get_adapters
from .splash import splash_sync
from .terminal import wait_for_exit


# ── layout constants ──────────────────────────────────────────────────────────
FILL  = "█"   # U+2588 FULL BLOCK
EMPTY = "░"   # U+2591 LIGHT SHADE
SPARK = " ▁▂▃▄▅▆▇█"  # 9 levels

_BW   = 28    # inner bar / spark width
_LW   = 7     # label column width
_VW   = 22    # value column min width (aligns notes)
_SEP  = "─" * 62
MIN_INTERVAL = 10

_PERIOD_SECS: dict[str, int] = {
    "5h":      5 * 3600,
    "daily":   86400,
    "7d":      7 * 86400,
    "weekly":  7 * 86400,
    "monthly": 30 * 86400,
}

# ── ANSI colours (suppressed when stdout is not a tty or NO_COLOR is set) ─────
def _c(code: str) -> str:
    if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
        return f"\033[{code}m"
    return ""

_GRN = _c("92")   # bright green  – usage low
_YLW = _c("93")   # bright yellow – usage mid / rate-hr
_RED = _c("91")   # bright red    – usage high
_CYN = _c("96")   # bright cyan   – spark
_BLU = _c("94")   # bright blue   – cycle elapsed
_RST = _c("0")    # reset


def _usage_color(pct: int) -> str:
    if pct >= 80: return _RED
    if pct >= 60: return _YLW
    return _GRN


# ── primitives ────────────────────────────────────────────────────────────────

def _dur(seconds: float) -> str:
    s = max(0, int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:  return f"{d}d {h:02d}h {m:02d}m"
    if h:  return f"{h}h {m:02d}m"
    return f"{m}m"


def _num(v: float, unit: str | None = None) -> str:
    for t, sfx in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(v) >= t:
            s = f"{v / t:.1f}{sfx}"
            return f"{s} {unit}" if unit else s
    s = f"{v:.0f}"
    return f"{s} {unit}" if unit else s


def _bar_content(value: float, total: float, color: str = "") -> str:
    """Return [████░░░░] fixed at _BW+2 visible chars."""
    n = min(_BW, max(0, int(round(value / total * _BW)))) if total > 0 else 0
    filled = f"{color}{FILL * n}{_RST}" if color and n > 0 else FILL * n
    return f"[{filled}{EMPTY * (_BW - n)}]"


def _spark_content(hourly: tuple | list) -> str:
    """Return spark track fixed at _BW+2 visible chars, newest column on the right."""
    now_h = datetime.now().hour
    slots = [float(hourly[(now_h - (_BW - 1 - i)) % 24]) for i in range(_BW)]
    peak  = max(slots) or 1.0
    chars = "".join(SPARK[min(8, int(v / peak * 8))] for v in slots)
    colored = f"{_CYN}{chars}{_RST}" if _CYN else chars
    return f" {colored} "   # 1 space + _BW chars + 1 space = _BW+2 visible


def _row(label: str, content: str, value: str = "", note: str = "") -> str:
    """Assemble one display row with fixed column widths."""
    v = f"{value:<{_VW}}" if note else value
    n = f"  {note}" if note else ""
    return f" {label:<{_LW}} {content}  {v}{n}".rstrip()


# ── per-record renderer ───────────────────────────────────────────────────────

def _render_block(record: NormalizedRecord, now: datetime) -> list[str]:
    lines: list[str] = []

    # ── header: TITLE  ·  Plan  ·  period ────────────────────────────────
    title = (record.product or record.record_id).upper()
    parts: list[str] = [title]
    if record.plan_name:
        parts.append(record.plan_name)
    if record.policy_reset:
        parts.append(record.policy_reset)
    lines.append(" " + "  ·  ".join(parts))
    lines.append(f" {_SEP}")

    # ── usage / quota bar ─────────────────────────────────────────────────
    if record.usage_value is not None:
        if record.quota_limit and record.quota_limit > 0:
            pct   = int(round(record.usage_value / record.quota_limit * 100))
            value = f"{_num(record.usage_value)} / {_num(record.quota_limit)}"
            lines.append(_row("Usage", _bar_content(record.usage_value, record.quota_limit, _usage_color(pct)), value, f"{pct}%"))
        else:
            lbl = (record.unit or "usage").capitalize()[:_LW]
            lines.append(_row(lbl, " " * (_BW + 2), _num(record.usage_value, record.unit)))

    # ── labeled sub-rows ─────────────────────────────────────────────────
    if record.session_count is not None and record.session_count > 0:
        lines.append(_row("Session", " " * (_BW + 2), f"{record.session_count} today"))

    if record.credits_value is not None:
        lines.append(_row("Credits", " " * (_BW + 2),
                          f"{int(record.credits_value)} remaining",
                          record.notes or ""))

    if record.cost_value is not None:
        lines.append(_row("Cost", " " * (_BW + 2),
                          f"${record.cost_value:.2f}",
                          record.cost_period or "today"))

    # ── notes: only show actionable lines, split on "; " ─────────────────
    if record.notes is not None and record.credits_value is None:
        for note_line in record.notes.split("; "):
            note_line = note_line.strip()
            if not note_line:
                continue
            # suppress "not eligible" noise; only show actionable credit states
            if "extra usage credits: not eligible" in note_line:
                continue
            # cap line length to fit terminal
            if len(note_line) > 58:
                note_line = note_line[:55] + "..."
            lines.append(f"   {_YLW}·{_RST} {note_line}")

    # ── hourly sparkline ─────────────────────────────────────────────────
    if record.hourly_usage is not None and any(v > 0 for v in record.hourly_usage):
        now_local = datetime.now()
        this_hr   = float(record.hourly_usage[now_local.hour]) \
                    if now_local.hour < len(record.hourly_usage) else 0.0
        unit_lbl  = record.unit or "units"
        lines.append(_row("Spark", _spark_content(record.hourly_usage),
                          f"{_num(this_hr)} {unit_lbl} this hr", "28h history"))

    # ── hourly rate-limit bar ─────────────────────────────────────────────
    if record.hourly_quota and record.hourly_quota > 0 and record.next_hourly_reset_at:
        now_local = datetime.now()
        this_hr   = float(record.hourly_usage[now_local.hour]) \
                    if record.hourly_usage and now_local.hour < len(record.hourly_usage) \
                    else 0.0
        pct_h = int(round(this_hr / record.hourly_quota * 100))
        note  = f"{pct_h}%"
        try:
            rdt  = datetime.fromisoformat(record.next_hourly_reset_at.replace("Z", "+00:00"))
            mins = int(max(0, (rdt - now).total_seconds()) // 60)
            hm   = rdt.astimezone().strftime("%H:%M")
            note = f"{pct_h}%  resets {hm}  {mins}m left"
        except ValueError:
            pass
        lines.append(_row("Rate/hr",
                          _bar_content(this_hr, record.hourly_quota, _usage_color(pct_h)),
                          f"{int(this_hr)} / {int(record.hourly_quota)}",
                          note))

    # ── period reset bar — label reflects the actual cadence ─────────────
    reset_iso  = record.observed_reset_at or record.estimated_reset_at
    period_sec = _PERIOD_SECS.get(record.policy_reset or "", 0)
    _CYCLE_LABEL: dict[str, str] = {
        "monthly": "Month", "weekly": "Week", "7d": "Week",
        "daily": "Day", "5h": "5h",
    }
    cycle_lbl = _CYCLE_LABEL.get(record.policy_reset or "", "Cycle")

    if reset_iso and period_sec > 0:
        try:
            rdt       = datetime.fromisoformat(reset_iso.replace("Z", "+00:00"))
            remaining = (rdt - now).total_seconds()
            if remaining > 0:
                elapsed = period_sec - remaining
                local_r = rdt.astimezone().strftime("%m/%d %H:%M")
                lines.append(_row(cycle_lbl,
                                  _bar_content(elapsed, period_sec, _BLU),
                                  _dur(remaining) + " left",
                                  f"resets {local_r}"))
            else:
                lines.append(_row(cycle_lbl, _bar_content(period_sec, period_sec, _BLU), "resetting soon"))
        except ValueError:
            pass

    # ── 7-day rolling window — only show if reset is within 8 days ───────
    if record.weekly_reset_at:
        try:
            rdt       = datetime.fromisoformat(record.weekly_reset_at.replace("Z", "+00:00"))
            remaining = (rdt - now).total_seconds()
            week_sec  = 7 * 86400
            if 0 < remaining <= 8 * 86400:
                elapsed = week_sec - remaining
                local_r = rdt.astimezone().strftime("%m/%d")
                lines.append(_row("Week",
                                  _bar_content(elapsed, week_sec, _BLU),
                                  _dur(remaining) + " left",
                                  f"resets {local_r}"))
        except ValueError:
            pass

    return lines


# ── sync + render frame ───────────────────────────────────────────────────────

def _sync_live_adapters() -> list[NormalizedRecord]:
    records: list[NormalizedRecord] = []
    for adapter in get_adapters():
        if getattr(adapter, "adapter_tier", "live") != "live":
            continue
        try:
            result = adapter.sync()
            if result.record and result.record.source_type != "unavailable":
                records.append(result.record)
        except Exception:  # noqa: BLE001
            pass
    return records


def _local_now_str() -> str:
    dt = datetime.now().astimezone()
    offset = dt.strftime("%z")  # e.g. "+0800"
    if len(offset) == 5:
        tz = f"UTC{offset[0]}{offset[1:3]}:{offset[3:5]}"
    else:
        tz = "local"
    return dt.strftime("%Y-%m-%d %H:%M:%S") + f" ({tz})"


def _render_frame(records: list[NormalizedRecord]) -> str:
    now = datetime.now(timezone.utc)
    lines: list[str] = [f"VBI Live  {_local_now_str()}", ""]
    for rec in records:
        lines.extend(_render_block(rec, now))
        lines.append("")
    return "\n".join(lines)


# ── public entry point ────────────────────────────────────────────────────────

def _ensure_utf8() -> None:
    """Force UTF-8 stdout so block characters render correctly on Windows."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def run_live(interval: int, once: bool) -> int:
    _ensure_utf8()
    refresh = max(MIN_INTERVAL, interval)

    # First sync runs through the splash so users see the robot logo + per-adapter
    # progress while sync is happening. Subsequent ticks go straight to the dashboard.
    records = splash_sync()
    os.system("cls" if os.name == "nt" else "clear")
    sys.stdout.write(_render_frame(records) + "\n")
    sys.stdout.flush()

    if once:
        return 0

    def _tick() -> None:
        records = _sync_live_adapters()
        frame = _render_frame(records)
        os.system("cls" if os.name == "nt" else "clear")
        sys.stdout.write(frame + "\n")
        sys.stdout.flush()

    # Ctrl+C inside `vbi live` exits cleanly. The home REPL (the launcher
    # process when this runs as a subprocess of `vbi`) detects the
    # interrupt and arms its own double-tap goodbye — we don't need a
    # nested home view here, which used to clash with the parent's stdin
    # cleanup and crash with EOFError on input().
    idle_footer = f"  (refreshing every {refresh}s  Ctrl+C/q to exit)"
    while True:
        print(idle_footer)
        if wait_for_exit(refresh):
            return 130
        _tick()
