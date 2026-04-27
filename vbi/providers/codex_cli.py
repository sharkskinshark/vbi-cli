"""Codex CLI local-telemetry adapter.

╔══════════════════════════════════════════════════════════════════════╗
║ DATA COLLECTION CONTRACT — DO NOT VIOLATE                            ║
╠══════════════════════════════════════════════════════════════════════╣
║ AUTO (works as soon as user logs in + uses Codex CLI at all):        ║
║   • Context tokens vs model window                                   ║
║   • 5h reset time + Week (7d) reset                                  ║
║   • Quota %  (primary.used_percent)                                  ║
║   • Plan name + subscription expiry (decoded from id_token JWT)      ║
║                                                                      ║
║ TRIGGER REQUIRED:  NONE.                                             ║
║   Every API call returns ``rate_limits`` in the response, and the    ║
║   CLI auto-writes it to ``token_count`` event in the session JSONL.  ║
║   ``/status`` is NOT required — it just renders the same auto data.  ║
║                                                                      ║
║ NO-PROBING RULE: We never call OpenAI API to fill gaps.              ║
║   The on-disk JSONL is always authoritative.                         ║
╚══════════════════════════════════════════════════════════════════════╝

Read-only. Never opens credential files or SQLite databases. Inspects only
``payload.info`` numeric fields, ``payload.rate_limits``, and the id_token
JWT claims. Other payload content is ignored.
"""

from __future__ import annotations

import base64
import json
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


SESSIONS_ROOT = Path.home() / ".codex" / "sessions"


def _next_midnight_local_utc() -> str:
    """ISO string of the next local midnight expressed in UTC."""
    from datetime import timedelta
    local_tz = datetime.now().astimezone().tzinfo
    now_local = datetime.now(local_tz)
    next_midnight = (now_local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def _next_hour_utc() -> str:
    """ISO string of the top of the next clock hour in UTC."""
    from datetime import timedelta
    now = datetime.now().astimezone()
    next_h = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return next_h.astimezone(timezone.utc).isoformat(timespec="seconds")


def _scan_all_sessions(root: Path) -> tuple[int, tuple[float, ...]]:
    """Return (sessions_today, hourly_24).

    sessions_today  = JSONL files last-modified today (local date)
    hourly_24       = count of files by modification hour (h0..h23 local today)
    """
    if not root.is_dir():
        return 0, tuple([0.0] * 24)

    local_tz = datetime.now().astimezone().tzinfo
    today_str = datetime.now(local_tz).strftime("%Y-%m-%d")
    hourly: list[float] = [0.0] * 24
    sessions_today = 0

    try:
        files = list(root.rglob("*.jsonl"))
    except OSError:
        return 0, tuple(hourly)

    for path in files:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        dt_local = datetime.fromtimestamp(mtime, tz=local_tz)
        if dt_local.strftime("%Y-%m-%d") == today_str:
            sessions_today += 1
            hourly[dt_local.hour] += 1

    return sessions_today, tuple(hourly)


def _parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _latest_jsonl(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    best: tuple[Path | None, float] = (None, -1.0)
    try:
        for path in root.rglob("*.jsonl"):
            if not path.is_file():
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > best[1]:
                best = (path, mtime)
    except OSError:
        return None
    return best[0]


def _last_token_event(path: Path) -> tuple[dict, datetime | None] | None:
    last_event: dict | None = None
    last_ts: datetime | None = None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("type", "")).strip() != "token_count":
                    continue
                ts = _parse_iso(row.get("timestamp"))
                if last_event is None or (ts is not None and (last_ts is None or ts >= last_ts)):
                    last_event = row
                    last_ts = ts
    except OSError:
        return None
    if last_event is None:
        return None
    return last_event, last_ts


def _read_jwt_claims() -> dict:
    """Decode id_token JWT and return the OpenAI auth claims dict."""
    auth_path = Path.home() / ".codex" / "auth.json"
    if not auth_path.is_file():
        return {}
    try:
        with auth_path.open(encoding="utf-8") as f:
            auth = json.load(f)
        id_token = (auth.get("tokens") or {}).get("id_token", "")
        parts = id_token.split(".")
        if len(parts) < 2:
            return {}
        padding = "=" * (4 - len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
        return claims.get("https://api.openai.com/auth") or {}
    except Exception:
        return {}


def _read_chatgpt_plan() -> tuple[str | None, str | None]:
    """Return (plan_name, subscription_until_iso) from id_token JWT claims."""
    claims = _read_jwt_claims()
    plan = claims.get("chatgpt_plan_type", "")
    plan_name = f"ChatGPT {plan.capitalize()}" if plan else None
    sub_until = claims.get("chatgpt_subscription_active_until")
    return plan_name, sub_until


def _empty_record(reason: str) -> NormalizedRecord:
    return NormalizedRecord(
        record_id=CodexCliAdapter.record_id,
        provider=CodexCliAdapter.provider,
        product=CodexCliAdapter.product,
        source_type="unavailable",
        updated_at=utc_now_iso(),
        confidence="unknown",
        blocked_reason=reason,
        evidence_source="local_telemetry",
    )


class CodexCliAdapter:
    record_id = "codex-cli"
    provider = "openai"
    product = "Codex CLI"
    adapter_tier = "live"

    def detect(self) -> ProviderAvailability:
        installed = SESSIONS_ROOT.is_dir()
        return ProviderAvailability(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            installed=installed,
            auth_state="not_required",
            evidence_paths=("~/.codex/sessions",) if installed else (),
            blocked_reason="" if installed else "no ~/.codex/sessions directory",
        )

    def read_cache(self) -> NormalizedRecord | None:
        return read_cache_record(self.record_id)

    def sync(self, force: bool = False) -> SyncResult:  # noqa: ARG002
        if not SESSIONS_ROOT.is_dir():
            record = _empty_record("no ~/.codex/sessions directory")
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message="Codex sessions directory not found.",
            )
        plan_name, sub_until = _read_chatgpt_plan()
        sessions_today, hourly = _scan_all_sessions(SESSIONS_ROOT)

        latest = _latest_jsonl(SESSIONS_ROOT)
        if latest is None:
            record = _empty_record("no jsonl session files")
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message="No Codex session JSONL files.",
            )
        result = _last_token_event(latest)
        if result is None:
            record = _empty_record(f"no token_count event in {latest.name}")
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message=f"No token_count event in {latest.name}.",
            )
        last_event, last_ts = result
        payload = last_event.get("payload", {}) or {}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        last_usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
        total_usage = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}

        context_window = _safe_int(info.get("model_context_window"))
        context_used = _safe_int(last_usage.get("total_tokens"))
        if context_used is None:
            context_used = _safe_int(total_usage.get("total_tokens"))

        # ── rate_limits: structured reset data from Codex API ────────────
        rl = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
        primary   = rl.get("primary")   if isinstance(rl.get("primary"), dict)   else {}
        secondary = rl.get("secondary") if isinstance(rl.get("secondary"), dict) else {}
        credits_rl = rl.get("credits")
        blocked   = rl.get("rate_limit_reached_type")

        def _ts_to_iso(ts) -> str | None:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds")
            except Exception:
                return None

        primary_reset_iso   = _ts_to_iso(primary.get("resets_at"))
        secondary_reset_iso = _ts_to_iso(secondary.get("resets_at"))
        primary_window_min  = _safe_int(primary.get("window_minutes"))    # 300 = 5h
        secondary_window_min = _safe_int(secondary.get("window_minutes")) # 10080 = 7d
        primary_used_pct    = primary.get("used_percent")                 # 0.0–1.0

        # derive policy_reset label from actual window
        if primary_window_min and primary_window_min % 60 == 0:
            rate_policy = f"{primary_window_min // 60}h"
        else:
            rate_policy = "5h"

        # weekly reset from secondary window
        weekly_iso: str | None = None
        if secondary_reset_iso:
            try:
                rdt = datetime.fromisoformat(secondary_reset_iso)
                remaining_7d = (rdt - datetime.now(timezone.utc)).total_seconds()
                if 0 < remaining_7d <= 8 * 86400:
                    weekly_iso = secondary_reset_iso
            except Exception:
                pass

        updated_at = (
            last_ts.astimezone(timezone.utc).isoformat(timespec="seconds")
            if last_ts is not None
            else utc_now_iso()
        )

        # ── notes ────────────────────────────────────────────────────────
        notes_parts: list[str] = []
        if blocked:
            notes_parts.append(f"rate limit reached: {blocked}")
        if isinstance(credits_rl, dict) and credits_rl.get("has_credits"):
            unlimited = credits_rl.get("unlimited", False)
            notes_parts.append("credits: unlimited" if unlimited else "credits: available")
        if primary_used_pct is not None:
            pct_display = min(100, int(primary_used_pct * 100))
            notes_parts.append(f"5h quota: {pct_display}% used")
        if sub_until:
            try:
                exp_dt = datetime.fromisoformat(sub_until.replace("Z", "+00:00"))
                days_left = (exp_dt - datetime.now(exp_dt.tzinfo)).days
                exp_local = exp_dt.astimezone().strftime("%Y-%m-%d")
                if days_left < 0:
                    # JWT past — auto-renewing plans (Plus/Pro) likely renewed
                    # already; the local id_token just hasn't refreshed yet.
                    notes_parts.append(
                        f"auth from {exp_local} ({-days_left}d old) — re-run codex to refresh"
                    )
                elif days_left <= 30:
                    notes_parts.append(f"plan active until {exp_local} ({days_left}d)")
                else:
                    notes_parts.append(f"plan active until {exp_local}")
            except ValueError:
                pass

        confidence = "medium" if primary_reset_iso else "low"

        common = dict(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            source_type="local_telemetry",
            updated_at=updated_at,
            confidence=confidence,
            plan_name=plan_name,
            unit="tokens",
            period="session",
            session_count=sessions_today if sessions_today > 0 else None,
            policy_reset=rate_policy,
            estimated_reset_at=primary_reset_iso,
            weekly_reset_at=weekly_iso,
            hourly_usage=hourly if any(v > 0 for v in hourly) else None,
            hourly_quota=None,
            next_hourly_reset_at=_next_hour_utc(),
            notes="; ".join(notes_parts) if notes_parts else None,
            evidence_source="local_telemetry+rate_limits",
        )

        if context_window is None or context_used is None or context_window <= 0:
            record = NormalizedRecord(
                **common,
                usage_value=float(context_used) if context_used is not None else None,
            )
        else:
            clamped = max(0, min(context_used, context_window))
            record = NormalizedRecord(
                **common,
                usage_value=float(clamped),
                quota_limit=float(context_window),
            )
        write_cache_record(record)
        return SyncResult(
            record_id=self.record_id,
            status="updated",
            record=record,
            message=f"{sessions_today} sessions today; context from {latest.name}",
        )

    def explain(self) -> str:
        return (
            "Reads the latest ~/.codex/sessions/**.jsonl token_count event "
            "and reports session context tokens vs model_context_window."
        )
