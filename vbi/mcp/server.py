"""vbi-cli MCP server (stdio).

First tool registered: ``status`` — returns each provider adapter's
cached normalized record. No sync, no network, cache-only — same
contract as ``vbi status`` on the CLI side.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..audit import has_critical, run_audit
from ..export_cmd import build_export_report, sanitize_report
from ..inventory import fetch_cached_status, run_inventory
from ..live import collect_live_records
from ..map_cmd import build_map_relationships
from ..registry import get_adapters
from ..runtime_cmd import scan_runtime_processes


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

    @mcp.tool()
    def map_relationships() -> dict[str, Any]:
        """Host-first map of detected AI tooling and their MCP servers.

        Equivalent to ``vbi map``. Returns a single object with five
        keys: apps, clis (lists of inventory records), extensions_by_host,
        mcp_servers_by_host (dicts keyed by host id), and
        cloud_hosted_mcp (claude.ai-side MCP server names).

        Read-only, scans local config files. Does not call inventory's
        heuristics path.
        """
        apps, clis, by_ext_host, mcp_by_host, cloud = build_map_relationships()
        return {
            "apps": [asdict(r) for r in apps],
            "clis": [asdict(r) for r in clis],
            "extensions_by_host": {
                host: [asdict(r) for r in records]
                for host, records in by_ext_host.items()
            },
            "mcp_servers_by_host": {
                host: sorted(servers) for host, servers in mcp_by_host.items()
            },
            "cloud_hosted_mcp": cloud,
        }

    @mcp.tool()
    def audit() -> dict[str, Any]:
        """Run vbi's GitHub release safety audit on the installed package.

        Equivalent to ``vbi audit``. Returns the list of Finding records
        plus a summary (counts by severity, has_critical flag). Empty
        findings list means PASS.
        """
        repo_root = Path(__file__).resolve().parent.parent.parent
        findings = run_audit(repo_root)
        by_severity: dict[str, int] = {}
        for f in findings:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        return {
            "findings": [asdict(f) for f in findings],
            "count": len(findings),
            "by_severity": by_severity,
            "has_critical": has_critical(findings),
        }

    @mcp.tool()
    def live_snapshot() -> list[dict[str, Any]]:
        """One-shot live usage snapshot from every live-tier provider.

        Equivalent to ``vbi live --once`` but data-only — no rendering,
        no redraw loop. Each entry is a freshly synced NormalizedRecord
        for one provider that has live telemetry (Antigravity, Claude
        Code, Codex CLI, Gemini CLI, OpenCode).

        Cost: 1-3s wall time depending on provider. Each adapter reads
        local files; no network calls. Use ``status`` instead if cached
        data is good enough — that's free.
        """
        return [asdict(rec) for rec in collect_live_records()]

    @mcp.tool()
    def export_report() -> dict[str, Any]:
        """Sanitized inventory + cached usage + audit findings, one object.

        Equivalent to ``vbi export`` but returned in-band instead of
        written to disk. Paths under the user's home are sanitized to
        ``~/...`` so the report is safe to share. Same JSON schema as
        the on-disk report.

        Same content is also exposed as a static MCP resource at
        ``vbi://report/latest`` — agents can use either based on their
        client's UX (tool call vs. resource fetch).
        """
        return sanitize_report(build_export_report())

    @mcp.resource("vbi://report/latest")
    def export_report_resource() -> str:
        """Full sanitized vbi report as a resource.

        Same payload as the export_report tool, but served as a static
        resource for clients that prefer the resource-oriented UX
        (e.g. attaching it to a conversation by URI).
        """
        import json
        return json.dumps(sanitize_report(build_export_report()), indent=2)

    @mcp.tool()
    def runtime_scan() -> list[dict[str, Any]]:
        """Scan local MCP / Node / Python runtime processes.

        Equivalent to ``vbi doctor runtime``. Each entry is a frozen
        ``RuntimeProcess`` dataclass serialized as a dict: pid, name,
        command, started_at, cpu_seconds, kind ('mcp'|'node'|'python'),
        and signature (used to identify duplicate groups).

        Cost: ~1-2s on Windows (spawns PowerShell to query
        Win32_Process). Avoid calling on every turn; cache results
        client-side when reasoning across multiple steps.
        """
        return [asdict(p) for p in scan_runtime_processes()]

    return mcp


def serve(transport: str = "stdio") -> None:
    """Run the MCP server on the chosen transport (default: stdio)."""
    mcp = build_server()
    mcp.run(transport=transport)
