"""End-to-end smoke test for vbi MCP server.

Spawns ``vbi mcp serve`` as a stdio subprocess, runs the MCP handshake,
lists registered tools, and calls ``status`` to confirm the round-trip
works. Prints a concise summary; exits 0 on success, non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import sys


def _decode_blocks(content) -> list[dict]:
    """FastMCP returns one TextContent per list element; collect all blocks
    that parse as JSON objects."""
    rows: list[dict] = []
    for block in content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            rows.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    return rows


def _decode_single_object(content) -> dict:
    """For tools that return a single dict, FastMCP serializes it as one
    TextContent block containing the full JSON object."""
    if not content:
        return {}
    text = getattr(content[0], "text", "") or ""
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vbi", "mcp", "serve"],
    )

    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            init_result = await session.initialize()
            print(f"[ok] handshake — server: {init_result.serverInfo.name} "
                  f"v{init_result.serverInfo.version}")

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"[ok] tools/list — {tool_names}")

            status_call = await session.call_tool("status", {})
            status_rows = _decode_blocks(status_call.content)
            print(f"[ok] tools/call status — {len(status_rows)} record(s)")
            for row in status_rows[:3]:
                print(f"     · {row.get('record_id', '?'):20s}  "
                      f"{str(row.get('source_type', '?')):16s}  {row.get('status', '?')}")
            if len(status_rows) > 3:
                print(f"     · ... {len(status_rows) - 3} more")

            inv_default = await session.call_tool("inventory", {})
            inv_obj = _decode_single_object(inv_default.content)
            print(f"[ok] tools/call inventory — tier1={len(inv_obj['tier1'])} "
                  f"tier2={len(inv_obj['tier2'])} status_keys={list(inv_obj.keys())}")

            inv_full = await session.call_tool(
                "inventory", {"with_status": True, "heuristics": True}
            )
            inv_full_obj = _decode_single_object(inv_full.content)
            print(f"[ok] tools/call inventory(with_status=True, heuristics=True) — "
                  f"tier1={len(inv_full_obj['tier1'])} "
                  f"tier2={len(inv_full_obj['tier2'])} "
                  f"status={len(inv_full_obj.get('status', {}))}")

    print("\n[done] vbi MCP stdio handshake working end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
