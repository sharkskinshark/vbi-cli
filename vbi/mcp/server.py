"""vbi-cli MCP server (stdio).

First tool registered: ``status`` — returns each provider adapter's
cached normalized record. No sync, no network, cache-only — same
contract as ``vbi status`` on the CLI side.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

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

    return mcp


def serve(transport: str = "stdio") -> None:
    """Run the MCP server on the chosen transport (default: stdio)."""
    mcp = build_server()
    mcp.run(transport=transport)
