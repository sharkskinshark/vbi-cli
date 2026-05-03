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

            rt_call = await session.call_tool("runtime_scan", {})
            rt_rows = _decode_blocks(rt_call.content)
            kinds: dict[str, int] = {}
            for row in rt_rows:
                kinds[row.get("kind", "?")] = kinds.get(row.get("kind", "?"), 0) + 1
            print(f"[ok] tools/call runtime_scan — {len(rt_rows)} process(es) "
                  f"by kind: {kinds}")
            for row in rt_rows[:2]:
                print(f"     · pid={row.get('pid')} kind={row.get('kind')} "
                      f"signature={row.get('signature', '?')[:60]}")

            map_call = await session.call_tool("map_relationships", {})
            map_obj = _decode_single_object(map_call.content)
            print(f"[ok] tools/call map_relationships — "
                  f"apps={len(map_obj.get('apps', []))} "
                  f"clis={len(map_obj.get('clis', []))} "
                  f"mcp_hosts={list(map_obj.get('mcp_servers_by_host', {}).keys())} "
                  f"cloud={len(map_obj.get('cloud_hosted_mcp', []))}")

            audit_call = await session.call_tool("audit", {})
            audit_obj = _decode_single_object(audit_call.content)
            print(f"[ok] tools/call audit — "
                  f"count={audit_obj.get('count')} "
                  f"critical={audit_obj.get('has_critical')} "
                  f"by_severity={audit_obj.get('by_severity')}")

            live_call = await session.call_tool("live_snapshot", {})
            live_rows = _decode_blocks(live_call.content)
            print(f"[ok] tools/call live_snapshot — {len(live_rows)} provider(s)")
            for row in live_rows[:3]:
                usage = row.get('usage_value')
                limit = row.get('quota_limit')
                usage_str = f"{usage}/{limit}" if usage is not None else "—"
                print(f"     · {row.get('record_id', '?'):20s}  {usage_str}")

            export_call = await session.call_tool("export_report", {})
            export_obj = _decode_single_object(export_call.content)
            print(f"[ok] tools/call export_report — schema={export_obj.get('schema_version')} "
                  f"vbi={export_obj.get('vbi_version')} "
                  f"tier1={len(export_obj.get('inventory', {}).get('tier1', []))} "
                  f"providers={len(export_obj.get('providers', {}))} "
                  f"audit={len(export_obj.get('audit', []))}")

            resources = await session.list_resources()
            uris = [str(r.uri) for r in resources.resources]
            print(f"[ok] resources/list — {uris}")
            res_read = await session.read_resource("vbi://report/latest")
            res_text = res_read.contents[0].text if res_read.contents else ""
            print(f"[ok] resources/read vbi://report/latest — {len(res_text)} chars")

            plan_call = await session.call_tool("cleanup_plan", {"groups": "mcp:*"})
            plan_obj = _decode_single_object(plan_call.content)
            print(f"[ok] tools/call cleanup_plan(groups='mcp:*') — "
                  f"groups={plan_obj.get('keep_count')} kills={plan_obj.get('kill_count')}")

            refuse_call = await session.call_tool("cleanup_apply", {})
            refuse_obj = _decode_single_object(refuse_call.content)
            print(f"[ok] tools/call cleanup_apply (no confirm) — "
                  f"applied={refuse_obj.get('applied')} "
                  f"reason={refuse_obj.get('reason', '')[:60]}")

            safe_call = await session.call_tool(
                "cleanup_apply", {"confirm": True, "groups": "nope:*"}
            )
            safe_obj = _decode_single_object(safe_call.content)
            print(f"[ok] tools/call cleanup_apply(confirm=True, groups='nope:*') — "
                  f"applied={safe_obj.get('applied')} "
                  f"stopped={safe_obj.get('stopped')} "
                  f"failed={safe_obj.get('failed')}")

    print("\n[done] vbi MCP stdio handshake working end-to-end.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
