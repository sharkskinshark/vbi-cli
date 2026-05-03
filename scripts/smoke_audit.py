"""Targeted: only call the audit tool, measure end-to-end time."""
from __future__ import annotations
import asyncio
import sys
import time


async def main() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "vbi", "mcp", "serve"],
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            print("[ok] handshake")
            t0 = time.monotonic()
            print("[..] calling audit", flush=True)
            try:
                result = await asyncio.wait_for(
                    session.call_tool("audit", {}), timeout=20
                )
            except asyncio.TimeoutError:
                print(f"[fail] audit timed out after {time.monotonic()-t0:.1f}s")
                return 1
            print(f"[ok] audit returned in {time.monotonic()-t0:.1f}s")
            print(f"     content blocks: {len(result.content)}")
            if result.content:
                text = getattr(result.content[0], "text", "")
                print(f"     first block (200 chars): {text[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
