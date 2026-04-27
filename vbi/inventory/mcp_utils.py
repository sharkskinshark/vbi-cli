"""Shared MCP-shaped JSON helpers used by both Tier 1 and Tier 2 scanners.

Read-only and whitelist-bounded. Yields only top-level ``mcpServers`` keys;
values inside ``mcpServers`` (command, args, env, url, tokens) are never
read or returned.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator


MCP_FILE_BASENAMES: frozenset[str] = frozenset(
    ["claude_desktop_config.json", "settings.json", "config.json"]
)


def mcp_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = [
        home / ".config",
        home / ".codex",
        home / ".gemini",
        home / ".claude",
        home / ".cursor",
        home / ".continue",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        appdata_path = Path(appdata)
        roots.extend(
            [
                appdata_path / "Claude",
                appdata_path / "Cursor" / "User",
                appdata_path / "Windsurf" / "User",
                appdata_path / "Code" / "User",
                appdata_path / "Antigravity",
                appdata_path / "Continue",
            ]
        )
    return roots


def mcp_filename_passes(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith(".json"):
        return False
    if lower in MCP_FILE_BASENAMES:
        return True
    if "mcp" in lower:
        return True
    return False


def walk_safe(root: Path, max_depth: int = 2) -> Iterator[Path]:
    try:
        if not root.is_dir() or root.is_symlink():
            return
    except OSError:
        return
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        current, depth = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if child.is_symlink():
                    continue
                if child.is_file():
                    yield child
                elif child.is_dir() and depth < max_depth:
                    pending.append((child, depth + 1))
            except OSError:
                continue


def iter_mcp_server_names() -> Iterator[tuple[Path, str]]:
    """Yield ``(file_path, server_name)`` pairs for every entry under
    ``mcpServers`` discovered in the whitelist roots.

    Reads only the keys of the top-level ``mcpServers`` dict. Values are
    never read.
    """

    seen_files: set[Path] = set()
    for root in mcp_roots():
        for path in walk_safe(root, max_depth=2):
            if path in seen_files:
                continue
            seen_files.add(path)
            if not mcp_filename_passes(path.name):
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            servers = data.get("mcpServers")
            if not isinstance(servers, dict):
                continue
            for server_name in servers.keys():
                if isinstance(server_name, str):
                    yield path, server_name

    # ── ~/.codex/config.toml: TOML, [mcp_servers.NAME] section headers ──
    # Codex CLI uses TOML, not JSON. We only read the keys of the
    # mcp_servers table — never command / args / url / env values.
    codex_toml = Path.home() / ".codex" / "config.toml"
    if codex_toml.is_file():
        try:
            import tomllib
            with codex_toml.open("rb") as f:
                tdata = tomllib.load(f)
        except (OSError, ValueError, ImportError) as _exc:
            tdata = None
        if isinstance(tdata, dict):
            tservers = tdata.get("mcp_servers")
            if isinstance(tservers, dict):
                for name in tservers.keys():
                    if isinstance(name, str):
                        yield codex_toml, name

    # ── ~/.claude.json: hidden, not in the whitelist; structured differently ──
    # Holds three MCP-shaped sources: top-level mcpServers, per-project
    # mcpServers, and the cloud-hosted claudeAiMcpEverConnected list. We only
    # read keys / list items — never values.
    claude_json = Path.home() / ".claude.json"
    if claude_json.is_file():
        try:
            data = json.loads(claude_json.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            top = data.get("mcpServers")
            if isinstance(top, dict):
                for name in top.keys():
                    if isinstance(name, str):
                        yield claude_json, name
            projects = data.get("projects")
            if isinstance(projects, dict):
                for proj_conf in projects.values():
                    if not isinstance(proj_conf, dict):
                        continue
                    proj_servers = proj_conf.get("mcpServers")
                    if not isinstance(proj_servers, dict):
                        continue
                    for name in proj_servers.keys():
                        if isinstance(name, str):
                            yield claude_json, name


def iter_claude_ai_hosted_mcp() -> Iterator[str]:
    """Yield names of cloud-hosted Claude.ai MCP integrations.

    These are configured in the user's Claude.ai web account (Settings →
    Integrations) and made available to Claude Code at session start. Source
    of truth: ``claudeAiMcpEverConnected`` list in ``~/.claude.json``.

    Names are stripped of the ``claude.ai`` prefix (e.g. "Figma", "Miro").
    Only list items are read — never any other ``~/.claude.json`` value.
    """
    claude_json = Path.home() / ".claude.json"
    if not claude_json.is_file():
        return
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    connected = data.get("claudeAiMcpEverConnected")
    if not isinstance(connected, list):
        return
    for name in connected:
        if not isinstance(name, str):
            continue
        prefix = "claude.ai "
        yield name[len(prefix):] if name.startswith(prefix) else name
