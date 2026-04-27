"""Unavailable provider scaffold.

This adapter proves the provider contract without reading local private data.
"""

from __future__ import annotations

from vbi.contracts import NormalizedRecord, ProviderAvailability, SyncResult, utc_now_iso


class UnavailableProviderAdapter:
    record_id = "scaffold/unavailable"
    provider = "scaffold"
    product = "Unavailable Provider Scaffold"
    adapter_tier = "scaffold"

    def detect(self) -> ProviderAvailability:
        return ProviderAvailability(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            installed=False,
            auth_state="not_applicable",
            evidence_paths=(),
            blocked_reason="adapter scaffold only; no provider evidence configured",
        )

    def read_cache(self) -> NormalizedRecord | None:
        return None

    def sync(self, force: bool = False) -> SyncResult:
        record = NormalizedRecord(
            record_id=self.record_id,
            provider=self.provider,
            product=self.product,
            source_type="unavailable",
            updated_at=utc_now_iso(),
            confidence="unknown",
            evidence_source="adapter_scaffold",
            blocked_reason="no provider evidence configured",
        )
        return SyncResult(
            record_id=self.record_id,
            status="unavailable",
            record=record,
            message="Provider scaffold returned an explicit unavailable record.",
        )

    def explain(self) -> str:
        return "Scaffold adapter used to validate degraded provider behavior."
