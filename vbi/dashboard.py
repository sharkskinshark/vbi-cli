"""Resident terminal dashboard for providers that report cached usage.

The dashboard renders only inventory records that map to a registered provider
adapter and have a cached ``NormalizedRecord``. It never calls
``adapter.sync()`` and never opens the network. Run ``vbi sync`` separately to
refresh provider caches.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from ._farewell import CtrlCExit
from .contracts import NormalizedRecord
from .inventory import fetch_cached_status, run_inventory
from .inventory.render import _format_cost_cell, _format_status_cell, _render_table


MIN_INTERVAL_SECONDS = 5


def _format_countdown(seconds: float) -> str:
    s = max(0, int(seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _format_reset_cell(cached: NormalizedRecord) -> str:
    reset_time = cached.observed_reset_at or cached.estimated_reset_at
    cadence = cached.policy_reset or ""
    if reset_time:
        try:
            dt = datetime.fromisoformat(reset_time.replace("Z", "+00:00"))
            local_dt = dt.astimezone()
            time_str = local_dt.strftime("%m/%d %H:%M")
            remaining = (dt - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                time_str = f"{time_str} ({_format_countdown(remaining)} left)"
            else:
                time_str = f"{time_str} (stale — run 'vbi sync')"
        except ValueError:
            time_str = reset_time[:16]
        return f"{cadence} -> {time_str}" if cadence else time_str
    return cadence or "-"


def _clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _render_dashboard_frame() -> str:
    tier1, _ = run_inventory(include_heuristics=False)
    status_map = fetch_cached_status(tier1)

    headers = (
        "Provider",
        "Source",
        "Usage",
        "Cost",
        "Reset",
        "Updated",
    )
    rows: list[tuple[str, ...]] = []
    for record in tier1:
        cached = status_map.get(record.record_id)
        if cached is None:
            continue
        if cached.source_type == "unavailable":
            continue
        reset = _format_reset_cell(cached)
        rows.append(
            (
                record.record_id,
                cached.source_type,
                _format_status_cell(cached),
                _format_cost_cell(cached),
                reset,
                cached.updated_at[:19].replace("T", " "),
            )
        )

    header_line = f"VBI Dashboard - {_now_text()}"
    if not rows:
        body = (
            "(no providers report cached usage yet)\n"
            "\n"
            "Tools detected by `vbi inventory` may have an adapter scaffold or no adapter at all.\n"
            "Run `vbi inventory --with-status` to inspect adapter coverage; live adapters land in ROADMAP T3."
        )
    else:
        body = _render_table(headers, rows)
    return f"{header_line}\n\n{body}"


def run_dashboard(interval: int, once: bool) -> int:
    refresh = max(interval, MIN_INTERVAL_SECONDS)
    if once:
        print(_render_dashboard_frame())
        return 0
    exit_handler = CtrlCExit()
    idle_footer = f"\n(refreshing every {refresh}s, Ctrl+C to exit)"
    while True:
        _clear_screen()
        print(_render_dashboard_frame())
        print(exit_handler.footer(idle_footer))
        try:
            time.sleep(refresh)
        except KeyboardInterrupt:
            if exit_handler.handle_interrupt():
                return 0
