"""Shared data contracts for provider adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

SourceType = Literal[
    "official_api",
    "billing_page",
    "local_telemetry",
    "policy_only",
    "manual",
    "unavailable",
]

Confidence = Literal["high", "medium", "low", "unknown"]
SyncStatus = Literal["updated", "fresh_cache", "unavailable", "failed", "skipped"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class ProviderAvailability:
    record_id: str
    provider: str
    product: str
    installed: bool
    auth_state: str
    evidence_paths: tuple[str, ...]
    blocked_reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedRecord:
    record_id: str
    provider: str
    product: str
    source_type: SourceType
    updated_at: str
    confidence: Confidence
    usage_value: float | None = None
    quota_limit: float | None = None
    unit: str | None = None
    period: str | None = None
    session_count: int | None = None
    observed_reset_at: str | None = None
    policy_reset: str | None = None
    estimated_reset_at: str | None = None
    cost_value: float | None = None
    cost_currency: str | None = None
    cost_period: str | None = None
    plan_name: str | None = None
    credits_value: float | None = None
    hourly_usage: tuple[float, ...] | None = None  # 24 slots (h0..h23 local today)
    hourly_quota: float | None = None              # per-hour hard limit
    next_hourly_reset_at: str | None = None        # UTC ISO, top of next hour
    weekly_reset_at: str | None = None             # UTC ISO, 7d rolling window reset
    notes: str | None = None                       # provider-specific extra context
    evidence_source: str | None = None
    blocked_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SyncResult:
    record_id: str
    status: SyncStatus
    record: NormalizedRecord | None
    message: str
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["record"] = self.record.to_dict() if self.record else None
        return data
