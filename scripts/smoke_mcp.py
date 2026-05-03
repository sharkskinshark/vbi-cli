"""End-to-end smoke test for vbi MCP server.

Spawns ``vbi mcp serve`` as a stdio subprocess, runs the MCP handshake,
lists registered tools, and calls ``status`` to confirm the round-trip
works. Prints a concise summary; exits 0 on success, non-zero on failure.
"""

from __future__ import annotations

import asyncio
import json
import sys


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

            call_result = await session.call_tool("status", {})
            rows: list[dict] = []
            for block in call_result.content:
                text = getattr(block, "text", None)
                if not text:
                    continue
                try:
                    rows.append(json.loads(text))
                except json.JSONDecodeError:
                    continue
            print(f"[ok] tools/call status — {len(rows)} record(s)")
            for row in rows[:3]:
                print(f"     · {row.get('record_id', '?'):20s}  "
                      f"{str(row.get('source_type', '?')):16s}  {row.get('status', '?')}")
            if len(rows) > 3:
                print(f"     · ... {len(rows) - 3} more")

    print("\n[done] vbi MCP stdio handshake working end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
