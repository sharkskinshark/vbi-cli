"""Inventory record schema and shared enums."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from vbi.contracts import utc_now_iso


InventoryStatus = Literal[
    "confirmed",
    "candidate",
    "configured",
    "found",
    "missing",
    "ignored",
    "unknown",
]

UsageStatus = Literal[
    "unavailable",
    "policy_only",
    "telemetry_possible",
    "official_api_possible",
]

Confidence = Literal["high", "medium", "low", "unknown"]
Tier = Literal["registry", "heuristic"]
Kind = Literal["cli", "app", "extension", "connector"]
Host = Literal["terminal", "desktop", "vscode", "mcp", "npm", "pipx", "system"]
AdapterStatus = Literal["none", "scaffold", "live"]


@dataclass(frozen=True)
class InventoryRecord:
    record_id: str
    display_name: str
    kind: Kind
    host: Host
    tier: Tier
    inventory_status: InventoryStatus
    confidence: Confidence
    usage_status: UsageStatus
    detected_at: str
    evidence_kind: str
    adapter_status: AdapterStatus = "none"
    evidence_summary: str | None = None
    blocked_reason: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


__all__ = [
    "AdapterStatus",
    "Confidence",
    "Host",
    "InventoryRecord",
    "InventoryStatus",
    "Kind",
    "Tier",
    "UsageStatus",
    "utc_now_iso",
]
