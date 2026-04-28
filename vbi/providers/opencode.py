"""OpenCode local-telemetry adapter (lightweight v1 — local files only).

╔══════════════════════════════════════════════════════════════════════╗
║ DATA COLLECTION CONTRACT — DO NOT VIOLATE                            ║
╠══════════════════════════════════════════════════════════════════════╣
║ AUTO (works as soon as user has run OpenCode at least once):         ║
║   • Sessions today      — count of log/<today>*.log files            ║
║   • Total sessions      — count of storage/session_diff/ses_*.json   ║
║   • Last activity       — newest log file mtime                      ║
║   • Configured providers — TOP-LEVEL keys of auth.json ONLY          ║
║                                                                      ║
║ NEVER:                                                               ║
║   • Read auth.json values (would expose API keys / OAuth tokens)     ║
║   • Parse opencode.db SQLite (schema undocumented; reverse-engineer  ║
║     would silently break on upstream changes)                        ║
║   • Hit opencode.ai cloud API (violates local-first principle)       ║
║                                                                      ║
║ SCOPE NOTE: OpenCode is a UI shell over BYO LLM providers; the real  ║
║ token/cost numbers live in the underlying provider's records         ║
║ (claude-code-cli, codex-cli, etc.) and are reported there. This      ║
║ adapter only adds OpenCode-specific session/configuration signals.   ║
╚══════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from vbi.cache import read_cache_record, write_cache_record
from vbi.contracts import (
    NormalizedRecord,
    ProviderAvailability,
    SyncResult,
    utc_now_iso,
)


def _data_root() -> Path:
    """Locate the OpenCode XDG data dir.

    Honour ``XDG_DATA_HOME`` if set, otherwise default to
    ``~/.local/share/opencode``. OpenCode follows the XDG convention on
    Windows too (it does NOT use %LOCALAPPDATA% for runtime data).
    """
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "opencode"
    return Path.home() / ".local" / "share" / "opencode"


def _list_providers_configured() -> list[str]:
    """Return sorted top-level provider keys from auth.json.

    Reads keys ONLY — values (which contain credentials) are never
    accessed or returned. If auth.json is missing or malformed, returns
    an empty list rather than failing the whole sync.
    """
    auth = _data_root() / "auth.json"
    if not auth.is_file():
        return []
    try:
        with auth.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return sorted(str(k) for k in data.keys())


def _sessions_today() -> int:
    """Count log files whose name starts with today's local date."""
    log_dir = _data_root() / "log"
    if not log_dir.is_dir():
        return 0
    today_prefix = datetime.now().strftime("%Y-%m-%d")
    n = 0
    try:
        for entry in log_dir.iterdir():
            if entry.is_file() and entry.name.startswith(today_prefix):
                n += 1
    except OSError:
        return 0
    return n


def _total_sessions() -> int:
    """Count session-diff JSON files in storage/session_diff/."""
    sd = _data_root() / "storage" / "session_diff"
    if not sd.is_dir():
        return 0
    n = 0
    try:
        for entry in sd.iterdir():
            if (
                entry.is_file()
                and entry.name.startswith("ses_")
                and entry.name.endswith(".json")
            ):
                n += 1
    except OSError:
        return 0
    return n


def _latest_activity_iso() -> str | None:
    """UTC-ISO timestamp of the most recently-touched log file, or None."""
    log_dir = _data_root() / "log"
    if not log_dir.is_dir():
        return None
    latest = 0.0
    try:
        for entry in log_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                m = entry.stat().st_mtime
            except OSError:
                continue
            if m > latest:
                latest = m
    except OSError:
        return None
    if latest == 0.0:
        return None
    return datetime.fromtimestamp(latest, tz=timezone.utc).isoformat(timespec="seconds")


def _empty_record() -> NormalizedRecord:
    return NormalizedRecord(
        record_id=OpenCodeAdapter.record_id,
        provider=OpenCodeAdapter.provider,
        product=OpenCodeAdapter.product,
        source_type="unavailable",
        updated_at=utc_now_iso(),
        confidence="unknown",
        blocked_reason="no ~/.local/share/opencode directory",
        evidence_source="local_telemetry",
    )


class OpenCodeAdapter:
    record_id = "opencode"
    provider = "opencode"
    product = "OpenCode"
    adapter_tier = "live"

    def detect(self) -> ProviderAvailability:
        installed = _data_root().is_dir()
        return ProviderAvailability(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            installed=installed,
            auth_state="not_required",
            evidence_paths=("~/.local/share/opencode",) if installed else (),
            blocked_reason="" if installed else "no ~/.local/share/opencode directory",
        )

    def read_cache(self) -> NormalizedRecord | None:
        return read_cache_record(self.record_id)

    def sync(self, force: bool = False) -> SyncResult:  # noqa: ARG002
        if not _data_root().is_dir():
            record = _empty_record()
            return SyncResult(
                record_id=self.record_id,
                status="unavailable",
                record=record,
                message="OpenCode data directory not found.",
            )

        sessions_today = _sessions_today()
        total = _total_sessions()
        latest = _latest_activity_iso()
        providers = _list_providers_configured()

        note_parts: list[str] = []
        if providers:
            note_parts.append(f"configured: {', '.join(providers)}")
        if total > 0:
            plural = "" if total == 1 else "s"
            note_parts.append(f"{total} session{plural} on disk")
        notes = "; ".join(note_parts) if note_parts else None

        record = NormalizedRecord(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            source_type="local_telemetry",
            updated_at=latest or utc_now_iso(),
            confidence="medium",
            session_count=sessions_today,
            unit="sessions",
            period="daily",
            notes=notes,
            evidence_source="local_telemetry",
        )
        write_cache_record(record)

        plural_today = "" if sessions_today == 1 else "s"
        return SyncResult(
            record_id=self.record_id,
            status="updated",
            record=record,
            message=(
                f"{sessions_today} session{plural_today} today, "
                f"{total} on disk, {len(providers)} providers configured"
            ),
        )

    def explain(self) -> str:
        return (
            "Reads ~/.local/share/opencode/log/<today>*.log filenames for "
            "today's session count and storage/session_diff/ses_*.json for "
            "the lifetime total. auth.json is read for TOP-LEVEL provider "
            "keys ONLY; values (credentials) are never opened. No SQLite "
            "parsing, no network."
        )
