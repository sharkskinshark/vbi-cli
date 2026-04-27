"""Gemini CLI local-telemetry adapter.

╔══════════════════════════════════════════════════════════════════════╗
║ DATA COLLECTION CONTRACT — DO NOT VIOLATE                            ║
╠══════════════════════════════════════════════════════════════════════╣
║ AUTO:                                                                ║
║   • Session count today / this month                                 ║
║   • API response count today (type=="gemini" messages — proxy)       ║
║   • Day reset (next PT midnight) — POLICY only, not real reset       ║
║                                                                      ║
║ NOT AVAILABLE LOCALLY (and we will NOT invent it):                   ║
║   • Token counts          — Gemini CLI does not log them             ║
║   • Quota / rate-limit %  — Gemini CLI does not store reset times    ║
║   • Real per-period reset — would require Gemini API quota endpoint  ║
║                                                                      ║
║ NO-PROBING RULE: We never call Google APIs to fill gaps.             ║
║   The honest answer is "session count + Day policy bar". Anything    ║
║   beyond that requires the Gemini API quota endpoint, which is out   ║
║   of scope for a local-telemetry adapter.                            ║
╚══════════════════════════════════════════════════════════════════════╝

Read-only. Never opens credential files (oauth_creds.json, mcp-oauth-tokens.json).
Inspects only session JSON files' ``type``, ``timestamp``, and top-level keys.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from vbi.cache import read_cache_record, write_cache_record
from vbi.contracts import (
    NormalizedRecord,
    ProviderAvailability,
    SyncResult,
    utc_now_iso,
)


GEMINI_TMP_ROOT = Path.home() / ".gemini" / "tmp"

# Google's Gemini API daily quota resets at midnight Pacific Time
# (PST/PDT — handled automatically by zoneinfo).
PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def _next_quota_reset_iso() -> str:
    """ISO string (UTC) of the next Gemini quota reset = PT midnight."""
    now_pt = datetime.now(PACIFIC_TZ)
    next_midnight_pt = (now_pt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_midnight_pt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _scan_sessions(root: Path) -> tuple[int, int, int, str | None]:
    """Return (sessions_today, sessions_month, responses_today, last_active_iso).

    sessions_today  = session files started today (local date)
    sessions_month  = session files started this calendar month
    responses_today = type=="gemini" messages in today's sessions
    last_active_iso = ISO timestamp of the most recent session start
    """
    if not root.is_dir():
        return 0, 0, 0, None

    local_tz = datetime.now().astimezone().tzinfo
    today_str = datetime.now(local_tz).strftime("%Y-%m-%d")
    month_str = today_str[:7]  # "YYYY-MM"

    sessions_today = 0
    sessions_month = 0
    responses_today = 0
    last_ts: datetime | None = None

    try:
        session_files = list(root.rglob("session-*.json"))
    except OSError:
        return 0, 0, 0, None

    for path in session_files:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue

        start_raw = data.get("startTime", "")
        if not isinstance(start_raw, str) or not start_raw:
            continue

        # Parse start time
        try:
            dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_local = dt.astimezone(local_tz)
        except ValueError:
            continue

        start_date = dt_local.strftime("%Y-%m-%d")
        start_month = start_date[:7]

        if start_month == month_str:
            sessions_month += 1
        if start_date == today_str:
            sessions_today += 1
            msgs = data.get("messages", [])
            if isinstance(msgs, list):
                for m in msgs:
                    if isinstance(m, dict) and m.get("type") == "gemini":
                        responses_today += 1

        if last_ts is None or dt > last_ts:
            last_ts = dt

    last_active = (
        last_ts.astimezone(timezone.utc).isoformat(timespec="seconds")
        if last_ts is not None
        else None
    )
    return sessions_today, sessions_month, responses_today, last_active


def _empty_record(reason: str) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=GeminiCliAdapter.record_id,
        provider=GeminiCliAdapter.provider,
        product=GeminiCliAdapter.product,
        source_type="unavailable",
        updated_at=utc_now_iso(),
        confidence="unknown",
        blocked_reason=reason,
        evidence_source="local_telemetry",
    )


class GeminiCliAdapter:
    record_id = "gemini-cli"
    provider = "google"
    product = "Gemini CLI"
    adapter_tier = "live"

    def detect(self) -> ProviderAvailability:
        installed = GEMINI_TMP_ROOT.is_dir()
        return ProviderAvailability(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            installed=installed,
            auth_state="not_required",
            evidence_paths=("~/.gemini/tmp",) if installed else (),
            blocked_reason="" if installed else "no ~/.gemini/tmp directory",
        )

    def read_cache(self) -> NormalizedRecord | None:
        return read_cache_record(self.record_id)

    def sync(self, force: bool = False) -> SyncResult:  # noqa: ARG002
        if not GEMINI_TMP_ROOT.is_dir():
            record = _empty_record("no ~/.gemini/tmp directory")
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message="Gemini CLI session directory not found.",
            )

        sessions_today, sessions_month, responses_today, last_active = _scan_sessions(
            GEMINI_TMP_ROOT
        )

        updated_at = utc_now_iso()

        # Primary metric: API responses today (type="gemini" message count).
        # Secondary: session count (sessions_today as session_count,
        # sessions_month shown via usage notes in message).
        record = NormalizedRecord(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            source_type="local_telemetry",
            updated_at=updated_at,
            confidence="low",  # session counts ≠ token counts; no quota data locally
            usage_value=float(responses_today) if responses_today > 0 else (
                float(sessions_month) if sessions_month > 0 else None
            ),
            unit="responses" if responses_today > 0 else "sessions",
            period="today" if responses_today > 0 else "monthly",
            session_count=sessions_today if sessions_today > 0 else None,
            policy_reset="daily",
            estimated_reset_at=_next_quota_reset_iso(),
            notes="no token/quota data — Gemini CLI doesn't log it locally",
            evidence_source="session_json",
        )
        write_cache_record(record)

        parts = []
        if responses_today > 0:
            parts.append(f"{responses_today} responses today")
        if sessions_today > 0:
            parts.append(f"{sessions_today} sessions today")
        if sessions_month != sessions_today and sessions_month > 0:
            parts.append(f"{sessions_month} sessions this month")
        if last_active:
            parts.append(f"last active {last_active[:10]}")
        msg = "; ".join(parts) if parts else "no Gemini CLI sessions found"

        return SyncResult(
            record_id=self.record_id,
            status="updated",
            record=record,
            message=msg,
        )

    def explain(self) -> str:
        return (
            "Reads ~/.gemini/tmp/**/session-*.json for session counts and "
            "type=gemini message counts (API response proxies). No token data "
            "is stored locally by Gemini CLI."
        )
