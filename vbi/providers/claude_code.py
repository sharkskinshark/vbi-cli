"""Claude Code local-telemetry adapter.

╔══════════════════════════════════════════════════════════════════════╗
║ DATA COLLECTION CONTRACT — DO NOT VIOLATE                            ║
╠══════════════════════════════════════════════════════════════════════╣
║ AUTO (works as soon as user logs in + uses Claude Code at all):      ║
║   • Tokens, Cost, Sessions, Hourly spark — from JSONL message.usage  ║
║   • Plan name — from ~/.claude.json oauthAccount                     ║
║   • Overage credit status — from ~/.claude.json overageCreditGrant…  ║
║                                                                      ║
║ TRIGGER REQUIRED (user MUST run inside Claude Code first):           ║
║   • 5h reset time   ← `/usage`                                       ║
║   • Week (7d) reset ← `/usage`  (same command, same text block)      ║
║                                                                      ║
║ NO-PROBING RULE: We never call claude.ai API to fill gaps.           ║
║   Cloudflare blocks bootstrap, and silent guesses are worse than a   ║
║   missing bar. When trigger data is absent we surface a hint note    ║
║   so the user knows exactly what command to run.                     ║
╚══════════════════════════════════════════════════════════════════════╝

Read-only. Never opens any file outside the projects root + ~/.claude.json
+ ~/.claude/stats-cache.json. Reads only ``message.usage`` fields, the row
``timestamp``, and assistant text blocks (for /usage regex). No credential
files are ever opened.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vbi.cache import read_cache_record, write_cache_record
from vbi.contracts import (
    NormalizedRecord,
    ProviderAvailability,
    SyncResult,
    utc_now_iso,
)


PROJECTS_ROOT = Path.home() / ".claude" / "projects"
STATS_CACHE = Path.home() / ".claude" / "stats-cache.json"
OUTPUT_PRICE_PER_TOKEN = 15.0 / 1_000_000  # Sonnet 4 approximate; not authoritative

# Hint surfaced when /usage was never run — tells the user the exact command
# instead of silently inventing a wrong reset time.
_USAGE_TRIGGER_HINT = "run /usage in Claude Code to populate 5h/Week reset"


def _today_start_local_timestamp() -> float:
    local_tz = datetime.now().astimezone().tzinfo
    start = datetime.now(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp()


_5H_RESET_RE = re.compile(
    r"5h\s+limit.*?(\d{1,2}:\d{2}\s*(?:AM|PM))\s*resets", re.IGNORECASE | re.DOTALL
)
_7D_RESET_RE = re.compile(
    r"7d\s+limit.*?resets\s+([A-Za-z]+\s+\d{1,2})", re.IGNORECASE | re.DOTALL
)


def _parse_usage_resets_from_jsonl() -> tuple[str | None, str | None]:
    """Scan recent JSONL files for /usage output; return (reset_5h_utc, reset_7d_utc).

    Both values are UTC ISO strings or None if not found.
    Only reads text blocks in assistant messages — never reads credentials.
    """
    if not PROJECTS_ROOT.is_dir():
        return None, None
    local_tz = datetime.now().astimezone().tzinfo
    now = datetime.now(local_tz)
    try:
        files = sorted(
            PROJECTS_ROOT.rglob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:40]
    except OSError:
        return None, None

    reset_5h: str | None = None
    reset_7d: str | None = None

    for path in files:
        if reset_5h and reset_7d:
            break
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or ("5h" not in line and "7d" not in line):
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(row, dict) or row.get("type") != "assistant":
                        continue
                    msg = row.get("message") or {}
                    for block in msg.get("content", []):
                        if not isinstance(block, dict) or block.get("type") != "text":
                            continue
                        text = block["text"]
                        # Extract 5h reset from this block
                        block_has_5h = False
                        if not reset_5h:
                            m = _5H_RESET_RE.search(text)
                            if m:
                                try:
                                    r = datetime.strptime(m.group(1).strip(), "%I:%M %p").replace(
                                        year=now.year, month=now.month, day=now.day,
                                        tzinfo=local_tz,
                                    )
                                    if r < now:
                                        r += timedelta(days=1)
                                    reset_5h = r.astimezone(timezone.utc).isoformat(timespec="seconds")
                                    block_has_5h = True
                                except ValueError:
                                    pass
                        # Only extract 7d from the same block as a 5h match to avoid
                        # matching discussion text that mentions past reset dates
                        if not reset_7d and block_has_5h:
                            m = _7D_RESET_RE.search(text)
                            if m:
                                try:
                                    r = datetime.strptime(
                                        f"{m.group(1).strip()} {now.year}", "%b %d %Y"
                                    ).replace(tzinfo=local_tz)
                                    # Roll forward in 7-day steps (max 2 = 14d back) to
                                    # recover from stale JSONL data without false positives
                                    steps = 0
                                    while r < now and steps < 2:
                                        r += timedelta(days=7)
                                        steps += 1
                                    if r >= now:
                                        reset_7d = r.astimezone(timezone.utc).isoformat(timespec="seconds")
                                except ValueError:
                                    pass
        except OSError:
            continue
    return reset_5h, reset_7d


def _read_overage_credit_status() -> str | None:
    """Read overageCreditGrantCache from ~/.claude.json.

    Returns a short human-readable note, or None if unavailable.
    """
    config_path = Path.home() / ".claude.json"
    if not config_path.is_file():
        return None
    try:
        with config_path.open(encoding="utf-8") as f:
            d = json.load(f)
        cache = d.get("overageCreditGrantCache") or {}
        if not cache:
            return None
        # take the most recent entry
        entry = next(iter(cache.values()), {})
        info = entry.get("info") or {}
        available = info.get("available", False)
        eligible = info.get("eligible", False)
        granted = info.get("granted", False)
        amount = info.get("amount_minor_units")
        currency = info.get("currency") or "USD"
        if granted and amount:
            dollars = amount / 100
            return f"extra usage credits granted: ${dollars:.2f} {currency}"
        if available and eligible:
            return "extra usage credits: eligible & available"
        if eligible:
            return "extra usage credits: eligible (not yet available)"
        return "extra usage credits: not eligible"
    except Exception:
        return None


def _read_stats_session_count() -> int | None:
    """Read today's sessionCount from ~/.claude/stats-cache.json (Claude Code's own cache)."""
    if not STATS_CACHE.is_file():
        return None
    try:
        with STATS_CACHE.open(encoding="utf-8") as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        for entry in data.get("dailyActivity", []):
            if entry.get("date") == today:
                return int(entry["sessionCount"])
    except Exception:
        pass
    return None


def _derive_plan_name() -> str | None:
    """Derive Claude plan name from ~/.claude.json signals (no API call)."""
    config_path = Path.home() / ".claude.json"
    if not config_path.is_file():
        return None
    try:
        with config_path.open(encoding="utf-8") as f:
            d = json.load(f)
        billing = (d.get("oauthAccount") or {}).get("organizationBillingType", "")
        if not billing:
            return None
        if billing != "stripe_subscription":
            return "Claude Free"
        s1m = d.get("s1mAccessCache") or {}
        has_1m = any(
            v.get("hasAccess", False) for v in s1m.values() if isinstance(v, dict)
        )
        has_opus_default = bool(d.get("hasOpusPlanDefault"))
        if has_1m or has_opus_default:
            return "Claude Max"
        return "Claude Pro"
    except Exception:
        return None


def _parse_iso_seconds(raw) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _scan_today() -> tuple[int, int, int, int, float, tuple[float, ...]] | None:
    """Return (total, output, sessions, requests, latest_ts, hourly_tokens_24).

    sessions: JSONL files with at least one assistant event today (conversations).
    requests: individual assistant turns today.
    hourly_tokens_24: 24 floats, index = local hour-of-day (0–23), tokens that hour.
    """
    if not PROJECTS_ROOT.is_dir():
        return None
    cutoff = _today_start_local_timestamp()
    local_tz = datetime.now().astimezone().tzinfo
    total = 0
    output = 0
    sessions = 0
    requests = 0
    latest = 0.0
    hourly: list[float] = [0.0] * 24
    try:
        files = list(PROJECTS_ROOT.rglob("*.jsonl"))
    except OSError:
        return None
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            continue
        file_had_event = False
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
                    if not isinstance(row, dict) or row.get("type") != "assistant":
                        continue
                    msg = row.get("message")
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    ts = _parse_iso_seconds(row.get("timestamp"))
                    if ts is None or ts < cutoff:
                        continue
                    inp = int(usage.get("input_tokens") or 0)
                    out = int(usage.get("output_tokens") or 0)
                    tokens = inp + out
                    total += tokens
                    output += out
                    requests += 1
                    file_had_event = True
                    if ts > latest:
                        latest = ts
                    h = datetime.fromtimestamp(ts, tz=local_tz).hour
                    hourly[h] += tokens
        except OSError:
            continue
        if file_had_event:
            sessions += 1
    return total, output, sessions, requests, latest, tuple(hourly)


def _empty_record() -> NormalizedRecord:
    return NormalizedRecord(
        record_id=ClaudeCodeAdapter.record_id,
        provider=ClaudeCodeAdapter.provider,
        product=ClaudeCodeAdapter.product,
        source_type="unavailable",
        updated_at=utc_now_iso(),
        confidence="unknown",
        blocked_reason="no ~/.claude/projects directory",
        evidence_source="local_telemetry+output_token_rate",
    )


class ClaudeCodeAdapter:
    record_id = "claude-code-cli"
    provider = "anthropic"
    product = "Claude Code"
    adapter_tier = "live"

    def detect(self) -> ProviderAvailability:
        installed = PROJECTS_ROOT.is_dir()
        return ProviderAvailability(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            installed=installed,
            auth_state="not_required",
            evidence_paths=("~/.claude/projects",) if installed else (),
            blocked_reason="" if installed else "no ~/.claude/projects directory",
        )

    def read_cache(self) -> NormalizedRecord | None:
        return read_cache_record(self.record_id)

    def sync(self, force: bool = False) -> SyncResult:  # noqa: ARG002
        if not PROJECTS_ROOT.is_dir():
            record = _empty_record()
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message="Claude Code projects directory not found.",
            )
        scanned = _scan_today()
        if scanned is None:
            record = _empty_record()
            return SyncResult(
                record_id=self.record_id,
                status="failed",
                record=record,
                message="Failed to scan ~/.claude/projects.",
            )
        total, output, sessions, requests, latest, hourly = scanned
        # Claude Code's policy IS 5h regardless of whether we know the reset time.
        # If /usage was never run, reset_at stays None and we emit a hint note —
        # we do NOT invent a "next midnight" value (that would lie about cadence).
        jsonl_5h_reset, jsonl_7d_reset = _parse_usage_resets_from_jsonl()
        rate_policy = "5h"
        reset_at = jsonl_5h_reset  # may be None — renderer hides bar when None

        plan_name = _derive_plan_name()
        overage_note = _read_overage_credit_status()
        # Compose notes: overage status + trigger hint (when /usage data missing)
        note_parts: list[str] = []
        if overage_note:
            note_parts.append(overage_note)
        if jsonl_5h_reset is None or jsonl_7d_reset is None:
            note_parts.append(_USAGE_TRIGGER_HINT)
        notes = "; ".join(note_parts) if note_parts else None
        # prefer Claude Code's own stats-cache (authoritative) over our JSONL file count
        stats_sessions = _read_stats_session_count()
        if stats_sessions is not None:
            sessions = stats_sessions
        if requests == 0:
            updated_at = utc_now_iso()
            record = NormalizedRecord(
                record_id=self.record_id,
                provider=self.provider,
                product=self.product,
                source_type="local_telemetry",
                updated_at=updated_at,
                confidence="medium",
                plan_name=plan_name,
                usage_value=0.0,
                session_count=sessions,
                unit="tokens",
                period="daily",
                policy_reset=rate_policy,
                estimated_reset_at=reset_at,
                weekly_reset_at=jsonl_7d_reset,
                cost_value=0.0,
                cost_currency="USD",
                cost_period="today",
                hourly_usage=hourly,
                notes=notes,
                evidence_source="local_telemetry+output_token_rate",
            )
            write_cache_record(record)
            return SyncResult(
                record_id=self.record_id,
                status="updated",
                record=record,
                message="No assistant events today.",
            )
        cost = round(output * OUTPUT_PRICE_PER_TOKEN, 4)
        updated_at = (
            datetime.fromtimestamp(latest, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
        record = NormalizedRecord(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            source_type="local_telemetry",
            updated_at=updated_at,
            confidence="medium",
            plan_name=plan_name,
            usage_value=float(total),
            session_count=sessions,
            unit="tokens",
            period="daily",
            policy_reset=rate_policy,
            estimated_reset_at=reset_at,
            weekly_reset_at=jsonl_7d_reset,
            cost_value=cost,
            cost_currency="USD",
            cost_period="today",
            hourly_usage=hourly,
            notes=notes,
            evidence_source="local_telemetry+output_token_rate",
        )
        write_cache_record(record)
        return SyncResult(
            record_id=self.record_id,
            status="updated",
            record=record,
            message=f"{sessions} sessions, {requests} turns today, {total:,} tokens",
        )

    def explain(self) -> str:
        return (
            "Reads ~/.claude/projects/**/*.jsonl for today's assistant "
            "events. Cost = output_tokens x $15/Mtok (Sonnet 4 approximate, "
            "non-authoritative)."
        )
