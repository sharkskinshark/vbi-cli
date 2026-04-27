"""Inventory scanner: read-only, public-safe footprint discovery."""

from __future__ import annotations

from vbi.contracts import NormalizedRecord
from vbi.registry import find_adapter

from .heuristics import run_heuristics
from .records import InventoryRecord
from .registry import all_aliases, scan_registry
from .render import render_inventory


def run_inventory(
    include_heuristics: bool = False,
) -> tuple[list[InventoryRecord], list[InventoryRecord] | None]:
    tier1 = scan_registry()
    if not include_heuristics:
        return tier1, None
    aliases = all_aliases()
    for record in tier1:
        aliases.add(record.record_id.lower())
    tier2 = run_heuristics(aliases)
    return tier1, tier2


def fetch_cached_status(
    records: list[InventoryRecord],
) -> dict[str, NormalizedRecord]:
    """Return cached normalized records for inventory entries that have an adapter.

    Cache-only: never calls ``adapter.sync()`` and never opens the network.
    Records whose adapter is missing, raises, or returns no cache are omitted.
    """

    result: dict[str, NormalizedRecord] = {}
    for record in records:
        if record.adapter_status == "none":
            continue
        adapter = find_adapter(record.record_id)
        if adapter is None:
            continue
        try:
            cached = adapter.read_cache()
        except Exception:  # noqa: BLE001 - adapter must not crash inventory
            continue
        if cached is None:
            continue
        result[record.record_id] = cached
    return result


__all__ = [
    "InventoryRecord",
    "fetch_cached_status",
    "render_inventory",
    "run_inventory",
]
