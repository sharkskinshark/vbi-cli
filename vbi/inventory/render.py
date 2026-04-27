"""Two-section terminal rendering for inventory records."""

from __future__ import annotations

from vbi.contracts import NormalizedRecord

from .records import InventoryRecord


def _render_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if rows:
        widths = [
            max(len(headers[i]), *(len(row[i]) for row in rows))
            for i in range(len(headers))
        ]
    else:
        widths = [len(h) for h in headers]

    def fmt(values: tuple[str, ...]) -> str:
        return "  ".join(value.ljust(widths[i]) for i, value in enumerate(values))

    lines = [fmt(headers), "  ".join("-" * widths[i] for i in range(len(headers)))]
    for row in rows:
        lines.append(fmt(row))
    return "\n".join(lines)


def _humanize_number(value: float, unit: str | None = None) -> str:
    if value is None:
        return "-"
    abs_v = abs(value)
    if abs_v >= 1_000_000_000:
        text = f"{value / 1_000_000_000:.1f}B"
    elif abs_v >= 1_000_000:
        text = f"{value / 1_000_000:.1f}M"
    elif abs_v >= 1_000:
        text = f"{value / 1_000:.1f}K"
    else:
        text = f"{value:.0f}"
    if unit:
        text = f"{text} {unit}"
    return text


def _format_status_cell(record: NormalizedRecord) -> str:
    if record.usage_value is not None and record.quota_limit and record.quota_limit > 0:
        used = _humanize_number(record.usage_value)
        cap = _humanize_number(record.quota_limit)
        pct = int(round(record.usage_value / record.quota_limit * 100))
        base = f"{used}/{cap} ({pct}%)"
    elif record.usage_value is not None:
        base = _humanize_number(record.usage_value, record.unit)
    elif record.source_type == "unavailable":
        return "unavailable"
    else:
        return record.source_type
    if record.session_count is not None and record.session_count > 0:
        base += f" / {record.session_count} sessions"
    if record.plan_name:
        base += f" [{record.plan_name}]"
    if record.credits_value is not None:
        base += f" / {_humanize_number(record.credits_value)} AI credits"
    return base


def _format_cost_cell(record: NormalizedRecord) -> str:
    if record.cost_value is None:
        return "-"
    currency = record.cost_currency or "USD"
    if currency.upper() == "USD":
        cell = f"${record.cost_value:,.2f}"
    else:
        cell = f"{record.cost_value:,.2f} {currency}"
    if record.cost_period:
        cell += f"/{record.cost_period}"
    return cell


def render_tier1(
    records: list[InventoryRecord],
    status_map: dict[str, NormalizedRecord] | None = None,
) -> str:
    records = [r for r in records if r.inventory_status != "missing"]
    if status_map is None:
        headers = (
            "Record",
            "Kind",
            "Host",
            "Inventory",
            "Adapter",
            "Usage Hint",
            "Evidence",
        )
        rows = [
            (
                r.record_id,
                r.kind,
                r.host,
                r.inventory_status,
                r.adapter_status,
                r.usage_status,
                r.evidence_summary or r.blocked_reason or "-",
            )
            for r in records
        ]
        return _render_table(headers, rows)

    headers = (
        "Record",
        "Kind",
        "Host",
        "Inventory",
        "Adapter",
        "Status",
        "Cost",
        "Source",
        "Updated",
    )
    rows = []
    for r in records:
        cached = status_map.get(r.record_id)
        if cached is not None:
            status_cell = _format_status_cell(cached)
            cost_cell = _format_cost_cell(cached)
            source_cell = cached.source_type
            updated_cell = cached.updated_at[:19].replace("T", " ")
        else:
            status_cell = "-"
            cost_cell = "-"
            source_cell = "-"
            updated_cell = "-"
        rows.append(
            (
                r.record_id,
                r.kind,
                r.host,
                r.inventory_status,
                r.adapter_status,
                status_cell,
                cost_cell,
                source_cell,
                updated_cell,
            )
        )
    return _render_table(headers, rows)


def render_tier2(records: list[InventoryRecord]) -> str:
    headers = ("Record", "Kind", "Host", "Inventory", "Confidence", "Evidence")
    rows = [
        (
            r.record_id,
            r.kind,
            r.host,
            r.inventory_status,
            r.confidence,
            r.evidence_summary or "-",
        )
        for r in records
    ]
    return _render_table(headers, rows)


def render_inventory(
    tier1: list[InventoryRecord],
    tier2: list[InventoryRecord] | None,
    status_map: dict[str, NormalizedRecord] | None = None,
) -> str:
    visible_tier1 = [r for r in tier1 if r.inventory_status != "missing"]
    header = "[Tier 1] Confirmed AI Tools (Known Registry)"
    if not visible_tier1:
        body = "(no AI tooling detected; the registry is a hint, not a checklist)"
    else:
        body = render_tier1(visible_tier1, status_map)
    sections: list[str] = [header, body]
    if tier2 is not None:
        sections.append("")
        sections.append("[Tier 2] Heuristic Candidates (Lower Confidence)")
        if tier2:
            sections.append(render_tier2(tier2))
        else:
            sections.append("(no candidates)")
    return "\n".join(sections)
