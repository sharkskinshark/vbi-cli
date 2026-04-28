"""`vbi map` — host-first hierarchy of detected AI tooling as a Mermaid graph.

Top of the diagram: IDE / Desktop apps (kind="app") and standalone CLIs.
Below each host: VS Code extensions and MCP servers attached to that host.
MCP attachment is inferred from which config file the server appears in
(e.g., a server in ``%APPDATA%/Claude/claude_desktop_config.json`` attaches
to Claude Desktop; one in ``~/.cursor/mcp.json`` attaches to Cursor).
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console
from rich.tree import Tree

from .inventory import run_inventory
from .inventory.mcp_utils import iter_claude_ai_hosted_mcp, iter_mcp_server_names
from .inventory.records import InventoryRecord


# record_ids of CLIs that act as AI coding agents (TUI-based, edit code,
# run commands). These get their own group in the map output so they
# don't sit next to generic platform CLIs (gh / gcloud / bq / vercel).
_AGENT_CLI_IDS: frozenset[str] = frozenset({
    "claude-code-cli",
    "codex-cli",
    "gemini-cli",
    "opencode",
    "aider",
})


def _inherited_mcp_from(app_record_id: str) -> str | None:
    """If ``app_record_id`` embeds another agent's runtime as an extension,
    return the source agent's record_id so the map can show an inheritance
    annotation (e.g. Antigravity ships the Claude Code extension and
    therefore inherits Claude Code's MCP configuration).

    Detection is filesystem-only: we look for an extension directory whose
    name starts with the source agent's slug. No file contents are read.
    """
    if app_record_id == "antigravity":
        ext_dir = Path.home() / ".antigravity" / "extensions"
        if not ext_dir.is_dir():
            return None
        try:
            for entry in ext_dir.iterdir():
                if entry.is_dir() and entry.name.lower().startswith("anthropic.claude-code"):
                    return "claude-code-cli"
        except OSError:
            return None
    return None


def _host_from_mcp_path(path: Path) -> str | None:
    """Map an MCP config file path to the host record_id it configures."""
    s = str(path).replace("\\", "/").lower()
    # Order matters: more specific paths first
    if "/.codex/" in s:
        return "codex-cli"
    if "/.gemini/" in s:
        return "gemini-cli"
    if "/.continue/" in s or "/continue/" in s:
        return "continue"
    if "/antigravity/" in s or "/.antigravity/" in s:
        return "antigravity"
    if "/.cursor/" in s or "/cursor/" in s:
        return "cursor"
    if "/windsurf/" in s:
        return "windsurf"
    if "/code/user/" in s:
        return "vscode"
    # Claude Code CLI's own ~/.claude.json (top-level + per-project mcpServers)
    if s.endswith("/.claude.json") or "/.claude/" in s:
        return "claude-code-cli"
    # Claude Desktop config lives under %APPDATA%/Claude/claude_desktop_config.json
    if "/claude/" in s and "claude_desktop_config" in s:
        return "claude-desktop"
    if "/claude/" in s:
        return "claude-desktop"
    return None


def _slug(s: str) -> str:
    """Mermaid-safe node id."""
    return "n_" + "".join(c if c.isalnum() else "_" for c in s)


def _kept(records: list[InventoryRecord]) -> list[InventoryRecord]:
    return [r for r in records if r.inventory_status in ("confirmed", "configured", "found")]


def _build_relationships() -> tuple[
    list[InventoryRecord],
    list[InventoryRecord],
    dict[str, list[InventoryRecord]],
    dict[str, set[str]],
    list[str],
]:
    """Return (apps, clis, extensions_by_host, mcp_servers_by_host, cloud_hosted)."""
    tier1, _ = run_inventory(include_heuristics=False)
    records = _kept(tier1)

    apps = [r for r in records if r.kind == "app"]
    clis = [r for r in records if r.kind == "cli"]
    exts = [r for r in records if r.kind == "extension"]

    by_ext_host: dict[str, list[InventoryRecord]] = {}
    for r in exts:
        by_ext_host.setdefault(r.host, []).append(r)

    mcp_by_host: dict[str, set[str]] = {}
    for path, server_name in iter_mcp_server_names():
        host_id = _host_from_mcp_path(path)
        if host_id is None:
            continue
        mcp_by_host.setdefault(host_id, set()).add(server_name)

    cloud_hosted = sorted(set(iter_claude_ai_hosted_mcp()))

    return apps, clis, by_ext_host, mcp_by_host, cloud_hosted


def render_tree() -> None:
    """Render host-first hierarchy as a colored tree directly to stdout."""
    apps, clis, by_ext_host, mcp_by_host, cloud_hosted = _build_relationships()

    console = Console()
    root = Tree("[bold]VBI[/bold]  [dim]local AI tooling map[/dim]")

    name_by_id = {r.record_id: r.display_name for r in apps + clis}

    if apps:
        ide_branch = root.add("[bold #ff8c1a]IDE / Desktop[/bold #ff8c1a]")
        for r in apps:
            node = ide_branch.add(f"[#ff8c1a]{r.display_name}[/#ff8c1a]  [dim]{r.record_id}[/dim]")
            for ext in by_ext_host.get(r.record_id, []):
                node.add(f"[grey70]ext  ·  {ext.display_name}[/grey70]")
            for mcp in sorted(mcp_by_host.get(r.record_id, set())):
                node.add(f"[#cc6699]mcp  ·  {mcp}[/#cc6699]")
            inherited_from = _inherited_mcp_from(r.record_id)
            if inherited_from:
                source_name = name_by_id.get(inherited_from, inherited_from)
                node.add(f"[dim italic]↳ inherits MCP from {source_name} (extension)[/dim italic]")

    agents = [r for r in clis if r.record_id in _AGENT_CLI_IDS]
    other_clis = [r for r in clis if r.record_id not in _AGENT_CLI_IDS]

    if agents:
        agent_branch = root.add("[bold #00b894]AI Coding Agents[/bold #00b894]")
        for r in agents:
            node = agent_branch.add(f"[#00b894]{r.display_name}[/#00b894]  [dim]{r.record_id}[/dim]")
            for mcp in sorted(mcp_by_host.get(r.record_id, set())):
                node.add(f"[#cc6699]mcp  ·  {mcp}[/#cc6699]")

    if other_clis:
        cli_branch = root.add("[bold #1a8cff]Terminal CLIs[/bold #1a8cff]")
        for r in other_clis:
            node = cli_branch.add(f"[#1a8cff]{r.display_name}[/#1a8cff]  [dim]{r.record_id}[/dim]")
            for mcp in sorted(mcp_by_host.get(r.record_id, set())):
                node.add(f"[#cc6699]mcp  ·  {mcp}[/#cc6699]")

    if cloud_hosted:
        cloud_branch = root.add("[bold #6699ff]Cloud-hosted MCP[/bold #6699ff]  [dim](claude.ai account)[/dim]")
        for name in cloud_hosted:
            cloud_branch.add(f"[#6699ff]cloud  ·  {name}[/#6699ff]")

    # MCP servers configured for hosts we don't have a record for (orphans)
    known = {r.record_id for r in apps} | {r.record_id for r in clis}
    orphan_hosts = sorted(set(mcp_by_host.keys()) - known)
    if orphan_hosts:
        orphan_branch = root.add("[bold dim]Other MCP-configured hosts[/bold dim]")
        for host in orphan_hosts:
            n = orphan_branch.add(f"[dim]{host}[/dim]")
            for mcp in sorted(mcp_by_host[host]):
                n.add(f"[#cc6699]mcp  ·  {mcp}[/#cc6699]")

    console.print(root)


def render_mermaid() -> str:
    """Render host-first hierarchy as Mermaid graph TD text."""
    apps, clis, by_ext_host, mcp_by_host, cloud_hosted = _build_relationships()

    out: list[str] = ["graph TD"]
    out.append("")

    # IDE / Desktop apps
    if apps:
        out.append("    %% IDEs and desktop apps")
        for r in apps:
            out.append(f'    {_slug(r.record_id)}["{r.display_name}"]:::host')
        out.append("")

    agents = [r for r in clis if r.record_id in _AGENT_CLI_IDS]
    other_clis = [r for r in clis if r.record_id not in _AGENT_CLI_IDS]

    if agents:
        out.append("    %% AI coding agents (TUI-based)")
        for r in agents:
            out.append(f'    {_slug(r.record_id)}(["{r.display_name}"]):::agent')
        out.append("")

    if other_clis:
        out.append("    %% Terminal CLIs (generic platform tools)")
        for r in other_clis:
            out.append(f'    {_slug(r.record_id)}(["{r.display_name}"]):::cli')
        out.append("")

    # VS Code extensions: vscode --> extension
    vscode_exts = by_ext_host.get("vscode", [])
    if vscode_exts:
        out.append("    %% VS Code extensions")
        for ext in vscode_exts:
            out.append(f'    {_slug("vscode")} --> {_slug(ext.record_id)}["{ext.display_name}"]:::ext')
        out.append("")

    # MCP servers attached to each host (dotted edge)
    if mcp_by_host:
        out.append("    %% MCP servers (dotted = MCP attachment)")
        for host_id, servers in sorted(mcp_by_host.items()):
            host_node = _slug(host_id)
            for s in sorted(servers):
                node = _slug(f"mcp_{host_id}_{s}")
                out.append(f'    {host_node} -.-> {node}(("{s}")):::mcp')
        out.append("")

    # Inheritance edges: e.g. Antigravity bundles the Claude Code extension
    # and therefore "inherits" Claude Code's MCP set without configuring its
    # own. We render this as a dotted labeled edge between the two hosts.
    inheritance_edges: list[tuple[str, str]] = []
    for r in apps:
        src = _inherited_mcp_from(r.record_id)
        if src:
            inheritance_edges.append((r.record_id, src))
    if inheritance_edges:
        out.append("    %% Inherited MCP relationships (extension-mediated)")
        for app_id, src_id in inheritance_edges:
            out.append(f'    {_slug(app_id)} -. "inherits MCP" .-> {_slug(src_id)}')
        out.append("")

    if cloud_hosted:
        out.append("    %% Cloud-hosted MCP (claude.ai account)")
        out.append('    cloud[/"Cloud (claude.ai)"/]:::cloud')
        host_node = _slug("claude-code-cli")
        out.append(f"    {host_node} -.-> cloud")
        for s in cloud_hosted:
            node = _slug(f"cloud_{s}")
            out.append(f'    cloud --> {node}(("{s}")):::cloudmcp')
        out.append("")

    out.append("    classDef host  fill:#fff5e6,stroke:#ff8c1a,stroke-width:2px;")
    out.append("    classDef agent fill:#e6f7f1,stroke:#00b894,stroke-width:2px;")
    out.append("    classDef cli   fill:#e6f3ff,stroke:#1a8cff,stroke-width:2px;")
    out.append("    classDef ext   fill:#f0f0f0,stroke:#888;")
    out.append("    classDef mcp   fill:#fef0f5,stroke:#cc6699;")
    out.append("    classDef cloud fill:#e6efff,stroke:#6699ff,stroke-width:2px;")
    out.append("    classDef cloudmcp fill:#f5f9ff,stroke:#6699ff;")

    return "\n".join(out)


def render_html(mermaid: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>VBI — AI tooling map</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 1100px; background: #1a1a1a; color: #eee; }}
  h1 {{ color: #ff8c1a; margin-bottom: 0.2em; }}
  .sub {{ color: #888; margin-top: 0; }}
  .legend {{ margin: 1em 0; font-size: 0.9em; }}
  .legend span {{ display: inline-block; padding: 2px 8px; margin-right: 0.5em; border-radius: 4px; border: 1px solid #555; }}
  .mermaid {{ background: white; padding: 1.5rem; border-radius: 8px; }}
</style>
</head><body>
<h1>VBI — Local AI tooling map</h1>
<p class="sub">Host-first hierarchy of detected AI tools on this machine.</p>
<div class="legend">
  <span style="background:#fff5e6;color:#000">IDE / Desktop</span>
  <span style="background:#e6f7f1;color:#000">AI Coding Agent</span>
  <span style="background:#e6f3ff;color:#000">Terminal CLI</span>
  <span style="background:#f0f0f0;color:#000">VS Code extension</span>
  <span style="background:#fef0f5;color:#000">MCP server</span>
  <span style="border-style:dotted">⋯ dotted = MCP attachment</span>
</div>
<pre class="mermaid">
{mermaid}
</pre>
<script>mermaid.initialize({{ startOnLoad: true, theme: 'default' }});</script>
</body></html>
"""


def run_map(mermaid: bool = False, html: bool = False, output: str | None = None) -> int:
    """Dispatch to the requested render mode.

    Default = colored tree in the terminal. Mermaid/HTML are opt-in for
    embedding (GitHub README / Notion) or browser viewing.
    """
    if html:
        out = render_html(render_mermaid())
    elif mermaid:
        out = render_mermaid()
    else:
        if output:
            # Render tree to a string by capturing the rich console
            from rich.console import Console
            buf = Console(record=True, force_terminal=False, width=120)
            apps, clis, by_ext_host, mcp_by_host, cloud_hosted = _build_relationships()
            tree_root = Tree("VBI  local AI tooling map")
            file_name_by_id = {r.record_id: r.display_name for r in apps + clis}
            if apps:
                ide_branch = tree_root.add("IDE / Desktop")
                for r in apps:
                    node = ide_branch.add(f"{r.display_name}  ({r.record_id})")
                    for ext in by_ext_host.get(r.record_id, []):
                        node.add(f"ext  ·  {ext.display_name}")
                    for mcp in sorted(mcp_by_host.get(r.record_id, set())):
                        node.add(f"mcp  ·  {mcp}")
                    inherited_from = _inherited_mcp_from(r.record_id)
                    if inherited_from:
                        source_name = file_name_by_id.get(inherited_from, inherited_from)
                        node.add(f"↳ inherits MCP from {source_name} (extension)")
            agents = [r for r in clis if r.record_id in _AGENT_CLI_IDS]
            other_clis = [r for r in clis if r.record_id not in _AGENT_CLI_IDS]
            if agents:
                agent_branch = tree_root.add("AI Coding Agents")
                for r in agents:
                    node = agent_branch.add(f"{r.display_name}  ({r.record_id})")
                    for mcp in sorted(mcp_by_host.get(r.record_id, set())):
                        node.add(f"mcp  ·  {mcp}")
            if other_clis:
                cli_branch = tree_root.add("Terminal CLIs")
                for r in other_clis:
                    node = cli_branch.add(f"{r.display_name}  ({r.record_id})")
                    for mcp in sorted(mcp_by_host.get(r.record_id, set())):
                        node.add(f"mcp  ·  {mcp}")
            if cloud_hosted:
                cloud_branch = tree_root.add("Cloud-hosted MCP (claude.ai account)")
                for name in cloud_hosted:
                    cloud_branch.add(f"cloud  ·  {name}")
            buf.print(tree_root)
            Path(output).write_text(buf.export_text(), encoding="utf-8")
            print(f"Wrote {output}")
            return 0
        render_tree()
        return 0

    if output:
        Path(output).write_text(out, encoding="utf-8")
        print(f"Wrote {output}")
    else:
        sys.stdout.write(out + "\n")
        sys.stdout.flush()
    return 0
