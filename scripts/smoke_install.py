"""End-to-end test: run `vbi mcp install` against a sandbox config,
then read the registered command and confirm an MCP handshake works
when invoked exactly as Claude Code would invoke it."""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sandbox = Path(tempfile.gettempdir()) / "vbi-mcp-install-smoke.json"
    if sandbox.exists():
        sandbox.unlink()

    env = os.environ.copy()
    env["VBI_MCP_CONFIG"] = str(sandbox)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "vbi", "mcp", "install",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        print(f"[fail] install exited {proc.returncode}")
        print(err.decode("utf-8", errors="replace"))
        return 1
    print(f"[ok] install completed, exit 0")

    config = json.loads(sandbox.read_text(encoding="utf-8"))
    entry = config.get("mcpServers", {}).get("vbi")
    if not entry:
        print(f"[fail] config missing mcpServers.vbi")
        return 1
    print(f"[ok] config has mcpServers.vbi: command={entry['command']!r} args={entry['args']!r}")

    params = StdioServerParameters(command=entry["command"], args=entry["args"])
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                print(f"[ok] handshake via registered entry — server={init.serverInfo.name} "
                      f"tools={[t.name for t in tools.tools]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[fail] handshake via registered entry failed: {exc}")
        return 1

    print("\n[done] end-to-end install + handshake works.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
