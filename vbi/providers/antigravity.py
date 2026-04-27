"""Antigravity local-telemetry adapter.

╔══════════════════════════════════════════════════════════════════════╗
║ DATA COLLECTION CONTRACT — DO NOT VIOLATE                            ║
╠══════════════════════════════════════════════════════════════════════╣
║ AUTO (works as soon as user logs in + uses Antigravity IDE at all):  ║
║   • Plan name + plan ID (state.vscdb userStatus)                     ║
║   • AI credits remaining (state.vscdb modelCredits)                  ║
║   • Subscription request count this month (cloudcode.log)            ║
║   • Hourly rate-limit usage (cloudcode.log timestamps)               ║
║   • Month reset time (first of next calendar month)                  ║
║                                                                      ║
║ TRIGGER REQUIRED:  NONE.                                             ║
║   The Antigravity extension auto-writes SQLite state on every API    ║
║   call and appends a ``recordCodeAssistMetrics`` line to the         ║
║   per-session cloudcode.log.                                         ║
║                                                                      ║
║ NO-PROBING RULE: We never call Google APIs to fill gaps.             ║
║   Local SQLite + log files are always authoritative.                 ║
╚══════════════════════════════════════════════════════════════════════╝

Read-only. No writes to disk. No credential values are extracted or surfaced.
Hand-rolled protobuf varint decoder; zero third-party dependencies.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vbi.cache import read_cache_record, write_cache_record
from vbi.contracts import (
    NormalizedRecord,
    ProviderAvailability,
    SyncResult,
    utc_now_iso,
)


# ── Paths ─────────────────────────────────────────────────────────────────────

def _db_path() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "antigravity" / "User" / "globalStorage" / "state.vscdb"


def _logs_root() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "antigravity" / "logs"


# ── Plan quota table ──────────────────────────────────────────────────────────

_PLAN_QUOTAS: dict[str, int] = {
    "Google AI Pro": 1000,
    "Google AI Ultra": 2000,
    "Google AI Free": 0,
}


# ── Hand-rolled protobuf helpers ──────────────────────────────────────────────

def _decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    val = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        val |= (b & 0x7F) << shift
        offset += 1
        shift += 7
        if not (b & 0x80):
            break
    return val, offset


def _parse_length_delimited(data: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = _decode_varint(data, offset)
    return data[offset : offset + length], offset + length


def _parse_proto_fields(data: bytes) -> dict[int, list[Any]]:
    """Parse a flat protobuf message into {field_num: [values]}.
    Only handles wire type 0 (varint) and 2 (length-delimited).
    """
    fields: dict[int, list[Any]] = {}
    offset = 0
    while offset < len(data):
        tag_byte = data[offset]
        field_num = tag_byte >> 3
        wire_type = tag_byte & 0x07
        offset += 1
        if wire_type == 0:
            val, offset = _decode_varint(data, offset)
            fields.setdefault(field_num, []).append(val)
        elif wire_type == 2:
            content, offset = _parse_length_delimited(data, offset)
            fields.setdefault(field_num, []).append(content)
        else:
            break
    return fields


# ── modelCredits extractor ────────────────────────────────────────────────────

def _extract_model_credits(raw_b64: str) -> dict[str, int]:
    """Return {key_name: int_value} from the modelCredits blob.

    Encoding: DB base64 → outer proto (repeated sub-messages with field1=name,
    field2=inner_blob) → inner_blob is a proto with field1=base64_ascii_str →
    that base64 decodes to a final proto with a varint value.
    """
    result: dict[str, int] = {}
    try:
        outer_bytes = base64.b64decode(raw_b64)
    except Exception:
        return result

    outer = _parse_proto_fields(outer_bytes)
    for sub_msg in outer.get(1, []):
        if not isinstance(sub_msg, bytes):
            continue
        sub = _parse_proto_fields(sub_msg)
        key_raw = sub.get(1, [None])[0]
        if not isinstance(key_raw, bytes):
            continue
        key = key_raw.decode("utf-8", errors="replace")

        inner_blob = sub.get(2, [None])[0]
        if not isinstance(inner_blob, bytes):
            continue
        inner = _parse_proto_fields(inner_blob)
        b64_ascii_raw = inner.get(1, [None])[0]
        if not isinstance(b64_ascii_raw, bytes):
            continue
        b64_str = b64_ascii_raw.decode("ascii", errors="replace")
        try:
            final_bytes = base64.b64decode(b64_str + "==")
            final = _parse_proto_fields(final_bytes)
            for fnum in (2, 1):
                if fnum in final and final[fnum]:
                    val = final[fnum][0]
                    if isinstance(val, int):
                        result[key] = val
                    break
        except Exception:
            pass
    return result


# ── userStatus extractor ──────────────────────────────────────────────────────

def _extract_user_status(raw_b64: str) -> tuple[str | None, str | None]:
    """Return (plan_name, plan_id) from the userStatus blob."""
    try:
        decoded = base64.b64decode(raw_b64)
    except Exception:
        return None, None

    text_repr = decoded.decode("latin-1")
    b64_segments = re.findall(r"[A-Za-z0-9+/]{20,}={0,2}", text_repr)

    combined = decoded.decode("utf-8", errors="replace") + "\n"
    for seg in b64_segments:
        try:
            inner = base64.b64decode(seg + "==")
            combined += inner.decode("utf-8", errors="replace") + "\n"
        except Exception:
            pass

    plan_name: str | None = None
    plan_id: str | None = None

    m = re.findall(r"Google AI (?:Pro|Ultra|Free)", combined)
    if m:
        plan_name = m[0]

    m = re.findall(r"g1-\w+-tier", combined)
    if m:
        plan_id = m[0]

    return plan_name, plan_id


# ── cloudcode.log subscription usage ─────────────────────────────────────────

_HOURLY_LIMIT = 50  # Antigravity subscription: 50 requests per hour


def _next_hour_utc() -> str:
    """ISO string of the top of the next clock hour in UTC."""
    now = datetime.now().astimezone()
    from datetime import timedelta
    next_h = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return next_h.astimezone(timezone.utc).isoformat(timespec="seconds")


def _first_of_next_month_utc() -> str:
    """ISO string of the first day of next calendar month (local midnight in UTC)."""
    now = datetime.now().astimezone()
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1,
                                 hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month = now.replace(month=now.month + 1, day=1,
                                 hour=0, minute=0, second=0, microsecond=0)
    return next_month.astimezone(timezone.utc).isoformat(timespec="seconds")


def _scan_subscription_usage(logs_root: Path) -> tuple[int, int, int, tuple[float, ...]]:
    """Return (request_count, session_count, requests_this_hour, hourly_24).

    request_count      = total recordCodeAssistMetrics this calendar month
    session_count      = distinct log dirs with activity this month
    requests_this_hour = events in the current clock hour (for 50/hr limit display)
    hourly_24          = 24 floats (h0..h23 local), requests each hour TODAY
    """
    if not logs_root.is_dir():
        return 0, 0, 0, tuple([0.0] * 24)

    now = datetime.now()
    local_tz = now.astimezone().tzinfo
    month_prefix = now.strftime("%Y%m")
    today_str = now.strftime("%Y-%m-%d")
    hour_start = now.replace(minute=0, second=0, microsecond=0)
    request_count = 0
    session_count = 0
    requests_this_hour = 0
    hourly: list[float] = [0.0] * 24
    _TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

    try:
        session_dirs = sorted(
            d for d in logs_root.iterdir()
            if d.is_dir() and d.name[:6] == month_prefix
        )
    except OSError:
        return 0, 0, 0, tuple(hourly)

    for sess_dir in session_dirs:
        cc_log = sess_dir / "cloudcode.log"
        if not cc_log.is_file():
            continue
        session_requests = 0
        try:
            with cc_log.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "recordCodeAssistMetrics" not in line:
                        continue
                    session_requests += 1
                    m = _TS_RE.match(line)
                    if not m:
                        continue
                    try:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                    if ts.strftime("%Y-%m-%d") == today_str:
                        hourly[ts.hour] += 1
                    if ts >= hour_start:
                        requests_this_hour += 1
        except OSError:
            continue
        if session_requests > 0:
            request_count += session_requests
            session_count += 1

    return request_count, session_count, requests_this_hour, tuple(hourly)


# ── Adapter ───────────────────────────────────────────────────────────────────

def _empty_record(reason: str) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=AntigravityAdapter.record_id,
        provider=AntigravityAdapter.provider,
        product=AntigravityAdapter.product,
        source_type="unavailable",
        updated_at=utc_now_iso(),
        confidence="unknown",
        blocked_reason=reason,
        evidence_source="local_telemetry",
    )


class AntigravityAdapter:
    record_id = "antigravity"
    provider = "google"
    product = "Antigravity"
    adapter_tier = "live"

    def detect(self) -> ProviderAvailability:
        db = _db_path()
        installed = db.is_file()
        return ProviderAvailability(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            installed=installed,
            auth_state="not_required",
            evidence_paths=(str(db),) if installed else (),
            blocked_reason="" if installed else "state.vscdb not found",
        )

    def read_cache(self) -> NormalizedRecord | None:
        return read_cache_record(self.record_id)

    def sync(self, force: bool = False) -> SyncResult:  # noqa: ARG002
        db = _db_path()
        if not db.is_file():
            record = _empty_record("state.vscdb not found")
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message="Antigravity state.vscdb not found.",
            )

        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        except sqlite3.OperationalError as exc:
            record = _empty_record(f"cannot open state.vscdb: {exc}")
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message=f"Cannot open state.vscdb: {exc}",
            )

        try:
            cur = conn.cursor()

            # 1. AI credits (PAYG wallet)
            cur.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("antigravityUnifiedStateSync.modelCredits",),
            )
            row = cur.fetchone()
            credits_remaining: int | None = None
            credits_minimum: int | None = None
            if row and row[0]:
                credits_data = _extract_model_credits(row[0])
                raw = credits_data.get("availableCreditsSentinelKey")
                if isinstance(raw, int):
                    credits_remaining = raw
                raw_min = credits_data.get("minimumCreditAmountForUsageKey")
                if isinstance(raw_min, int):
                    credits_minimum = raw_min

            # 2. Plan info
            cur.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("antigravityUnifiedStateSync.userStatus",),
            )
            row = cur.fetchone()
            plan_name: str | None = None
            plan_id: str | None = None
            if row and row[0]:
                plan_name, plan_id = _extract_user_status(row[0])

        finally:
            conn.close()

        # 3. Subscription usage from cloudcode.log (current calendar month)
        sub_requests, log_sessions, this_hour, hourly = _scan_subscription_usage(_logs_root())
        monthly_quota = _PLAN_QUOTAS.get(plan_name or "", 0) if plan_name else 0

        updated_at = utc_now_iso()

        # Primary story: subscription requests vs. monthly quota (if quota > 0)
        if monthly_quota > 0:
            usage_value: float | None = float(sub_requests)
            quota_limit: float | None = float(monthly_quota)
            unit = "requests"
            period = "monthly"
        elif sub_requests > 0:
            usage_value = float(sub_requests)
            quota_limit = None
            unit = "requests"
            period = "monthly"
        else:
            usage_value = None
            quota_limit = None
            unit = None
            period = None

        record = NormalizedRecord(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            source_type="local_telemetry",
            updated_at=updated_at,
            confidence="medium",
            usage_value=usage_value,
            quota_limit=quota_limit,
            unit=unit,
            period=period,
            session_count=log_sessions if log_sessions > 0 else None,
            policy_reset="monthly",
            estimated_reset_at=_first_of_next_month_utc(),
            plan_name=plan_name,
            credits_value=float(credits_remaining) if credits_remaining is not None else None,
            notes=f"min {credits_minimum} to use" if credits_minimum is not None else None,
            hourly_usage=hourly,
            hourly_quota=float(_HOURLY_LIMIT),
            next_hourly_reset_at=_next_hour_utc(),
            evidence_source="sqlite_state+cloudcode_log",
        )
        write_cache_record(record)

        parts = []
        if plan_name:
            parts.append(plan_name)
        if credits_remaining is not None:
            parts.append(f"{credits_remaining} AI credits")
        if monthly_quota > 0:
            parts.append(f"{sub_requests}/{monthly_quota} subscription requests")
        if log_sessions > 0:
            parts.append(f"{log_sessions} sessions this month")
        msg = "; ".join(parts) if parts else "parsed state.vscdb"

        return SyncResult(
            record_id=self.record_id,
            status="updated",
            record=record,
            message=msg,
        )

    def explain(self) -> str:
        return (
            "Reads %APPDATA%/antigravity/User/globalStorage/state.vscdb (SQLite + "
            "protobuf) for AI credits remaining and plan name. "
            "Scans cloudcode.log for subscription request counts (current calendar month)."
        )
