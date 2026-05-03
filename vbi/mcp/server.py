"""vbi-cli MCP server (stdio).

First tool registered: ``status`` — returns each provider adapter's
cached normalized record. No sync, no network, cache-only — same
contract as ``vbi status`` on the CLI side.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..inventory import fetch_cached_status, run_inventory
from ..registry import get_adapters


def build_server() -> Any:
    """Construct the FastMCP server lazily so the import doesn't fail
    when the optional ``mcp`` SDK isn't installed."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit(
            "MCP SDK not installed. Install with: pip install 'vbi-cli[mcp]'"
        ) from exc

    mcp = FastMCP("vbi")

    @mcp.tool()
    def status() -> list[dict[str, Any]]:
        """Return cached provider status records (cache-only, no sync, no network).

        Equivalent to running ``vbi status`` on the CLI. Each entry is a
        provider adapter's last cached NormalizedRecord, or a stub row
        when the adapter has no cached record yet.
        """
        rows: list[dict[str, Any]] = []
        for adapter in get_adapters():
            record = adapter.read_cache()
            record_id = getattr(adapter, "record_id", "unknown")
            if record is None:
                rows.append({
                    "record_id": record_id,
                    "source_type": "unavailable",
                    "confidence": "unknown",
                    "status": "no cached record",
                })
            else:
                row = asdict(record)
                row["status"] = record.blocked_reason or "ok"
                rows.append(row)
        return rows

    @mcp.tool()
    def inventory(
        with_status: bool = False,
        heuristics: bool = False,
    ) -> dict[str, Any]:
        """Discover installed AI tooling on the local machine.

        Equivalent to ``vbi inventory``. Returns a single structured
        object with tier1 (confirmed registry hits), tier2 (heuristic
        matches when ``heuristics=True``), and an optional ``status``
        map (cached usage records keyed by record_id when
        ``with_status=True``).

        Read-only, no network, no credentials.
        """
        tier1, tier2 = run_inventory(include_heuristics=heuristics)
        result: dict[str, Any] = {
            "tier1": [asdict(r) for r in tier1],
            "tier2": [asdict(r) for r in tier2] if heuristics else [],
        }
        if with_status:
            status_map = fetch_cached_status(tier1)
            result["status"] = {
                record_id: asdict(rec) for record_id, rec in status_map.items()
            }
        return result

    return mcp


def serve(transport: str = "stdio") -> None:
    """Run the MCP server on the chosen transport (default: stdio)."""
    mcp = build_server()
    mcp.run(transport=transport)
