"""Resident terminal dashboard for providers that report cached usage.

The dashboard renders only inventory records that map to a registered provider
adapter and have a cached ``NormalizedRecord``. It never calls
``adapter.sync()`` and never opens the network. Run ``vbi sync`` separately to
refresh provider caches.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from .contracts import NormalizedRecord
from .inventory import fetch_cached_status, run_inventory
from .inventory.render import _humanize_number
from .terminal import wait_for_exit


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


def _format_synced_ago(updated_at_iso: str) -> str:
    """Compact relative time: 5s / 5m / 2h / 1d ago."""
    try:
        dt = datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return updated_at_iso[:10]
    sec = int((datetime.now(timezone.utc) - dt).total_seconds())
    if sec < 60:    return f"{sec}s ago"
    if sec < 3600:  return f"{sec // 60}m ago"
    if sec < 86400: return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _format_cost(rec: NormalizedRecord) -> str | None:
    if rec.cost_value is None:
        return None
    currency = (rec.cost_currency or "USD").upper()
    base = f"${rec.cost_value:,.2f}" if currency == "USD" else f"{rec.cost_value:,.2f} {currency}"
    return f"{base}/{rec.cost_period}" if rec.cost_period else base


def _format_provider_block(rec: NormalizedRecord, name_width: int) -> str:
    """Three lines per provider:
        ▸ <name>          <plan>
          <usage · sessions · credits · cost>
          <cadence · → reset-time · time-left · synced ago>
    """
    # ── line 1: name + plan ──────────────────────────────────────────────
    name_field = f"▸ {rec.record_id}".ljust(name_width + 2)
    plan = rec.plan_name or ""
    line1 = f"{name_field}  {plan}".rstrip()

    # ── line 2: usage / sessions / credits / cost ────────────────────────
    parts: list[str] = []
    if rec.usage_value is not None and rec.quota_limit and rec.quota_limit > 0:
        used = _humanize_number(rec.usage_value)
        cap  = _humanize_number(rec.quota_limit)
        pct  = int(round(rec.usage_value / rec.quota_limit * 100))
        parts.append(f"{used}/{cap} ({pct}%)")
    elif rec.usage_value is not None:
        parts.append(_humanize_number(rec.usage_value, rec.unit))
    if rec.session_count:
        s = "session" if rec.session_count == 1 else "sessions"
        parts.append(f"{rec.session_count} {s}")
    if rec.credits_value is not None:
        parts.append(f"{int(rec.credits_value)} credits")
    cost = _format_cost(rec)
    if cost is not None:
        parts.append(cost)
    if not parts:
        parts.append(rec.source_type)
    line2 = "  " + " · ".join(parts)

    # ── line 3: reset cadence + target time + remaining + synced ─────────
    parts = []
    if rec.policy_reset:
        parts.append(rec.policy_reset)
    reset_iso = rec.observed_reset_at or rec.estimated_reset_at
    if reset_iso:
        try:
            rdt   = datetime.fromisoformat(reset_iso.replace("Z", "+00:00"))
            local = rdt.astimezone()
            parts.append(f"→ {local.strftime('%m/%d %H:%M')}")
            remaining = (rdt - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                parts.append(f"{_format_countdown(remaining)} left")
            else:
                parts.append("stale — run 'vbi sync'")
        except ValueError:
            pass
    parts.append(f"synced {_format_synced_ago(rec.updated_at)}")
    line3 = "  " + " · ".join(parts)

    return "\n".join((line1, line2, line3))


def _clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _render_dashboard_frame() -> str:
    tier1, _ = run_inventory(include_heuristics=False)
    status_map = fetch_cached_status(tier1)

    valid: list[NormalizedRecord] = []
    for record in tier1:
        cached = status_map.get(record.record_id)
        if cached is None or cached.source_type == "unavailable":
            continue
        valid.append(cached)

    header_line = f"VBI Dashboard · {_now_text()}"
    if not valid:
        body = (
            "(no providers report cached usage yet)\n"
            "\n"
            "Tools detected by `vbi inventory` may have an adapter scaffold or no adapter at all.\n"
            "Run `vbi inventory --with-status` to inspect adapter coverage."
        )
        return f"{header_line}\n\n{body}"

    name_width = max(len(c.record_id) for c in valid)
    blocks = [_format_provider_block(c, name_width) for c in valid]
    return f"{header_line}\n\n" + "\n\n".join(blocks)


def run_dashboard(interval: int, once: bool) -> int:
    refresh = max(interval, MIN_INTERVAL_SECONDS)
    if once:
        print(_render_dashboard_frame())
        return 0
    # Ctrl+C inside `vbi dashboard` exits cleanly. The home REPL (when
    # this runs as a subprocess of `vbi`) catches the interrupt itself
    # and arms the double-tap goodbye; nesting another REPL here racing
    # against the parent's stdin teardown was causing EOFError crashes.
    idle_footer = f"\n(refreshing every {refresh}s, Ctrl+C/q to exit)"
    while True:
        _clear_screen()
        print(_render_dashboard_frame())
        print(idle_footer)
        if wait_for_exit(refresh):
            return 130
