"""Tier 2: heuristic discovery surfaces. Read-only, allowlist-bounded.

Each surface declares its matched fields and evidence format. Surfaces backed
by external commands enforce timeouts and skip silently on absence or non-zero
exit.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .mcp_utils import iter_mcp_server_names
from .records import Confidence, InventoryRecord, utc_now_iso


AI_KEYWORDS: frozenset[str] = frozenset(
    [
        "ai",
        "llm",
        "gpt",
        "claude",
        "gemini",
        "codex",
        "copilot",
        "agent",
        "mcp",
        "openai",
        "anthropic",
        "huggingface",
        "ollama",
        "mistral",
        "perplexity",
        "cursor",
        "windsurf",
        "continue",
    ]
)

VSCODE_AI_CATEGORIES: frozenset[str] = frozenset(
    ["ai", "machine learning", "chat"]
)

SUBPROCESS_TIMEOUT_SECONDS = 10
MAX_SUBPROCESS_BYTES = 1_000_000


def _tokenize(name: str) -> list[str]:
    return [t.lower() for t in re.split(r"[^A-Za-z0-9]+", name) if t]


def _name_keyword_match(name: str) -> str | None:
    for token in _tokenize(name):
        if token in AI_KEYWORDS:
            return token
    return None


def _is_aliased(aliases: set[str], *names: str) -> bool:
    for name in names:
        lower = name.lower()
        if lower in aliases:
            return True
        for token in _tokenize(name):
            if token in aliases:
                return True
    return False


def _candidate(
    *,
    record_id: str,
    display_name: str,
    kind: str,
    host: str,
    confidence: Confidence,
    evidence_kind: str,
    evidence_summary: str,
) -> InventoryRecord:
    return InventoryRecord(
        record_id=record_id,
        display_name=display_name,
        kind=kind,  # type: ignore[arg-type]
        host=host,  # type: ignore[arg-type]
        tier="heuristic",
        inventory_status="candidate",
        confidence=confidence,
        usage_status="unavailable",
        detected_at=utc_now_iso(),
        evidence_kind=evidence_kind,
        evidence_summary=evidence_summary,
    )


def _run_subprocess(name: str, args: list[str]) -> str | None:
    binary = shutil.which(name)
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            text=True,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout or ""
    if len(output) > MAX_SUBPROCESS_BYTES:
        return None
    return output


def _scan_path(aliases: set[str]) -> list[InventoryRecord]:
    records: list[InventoryRecord] = []
    seen: set[str] = set()
    raw_path = os.environ.get("PATH", "")
    for entry in raw_path.split(os.pathsep):
        if not entry:
            continue
        path = Path(entry)
        try:
            if not path.is_dir():
                continue
            children = list(path.iterdir())
        except OSError:
            continue
        for item in children:
            try:
                if not item.is_file():
                    continue
            except OSError:
                continue
            stem = item.stem.lower()
            if not stem or stem in seen:
                continue
            if _is_aliased(aliases, stem):
                continue
            if _name_keyword_match(stem) is None:
                continue
            seen.add(stem)
            records.append(
                _candidate(
                    record_id=f"heuristic:path:{stem}",
                    display_name=item.stem,
                    kind="cli",
                    host="terminal",
                    confidence="low",
                    evidence_kind="path_heuristic",
                    evidence_summary=f"path:name={stem}",
                )
            )
    return records


def _scan_npm_global(aliases: set[str]) -> list[InventoryRecord]:
    raw = _run_subprocess("npm", ["ls", "-g", "--json", "--depth=0"])
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    deps = data.get("dependencies") if isinstance(data, dict) else None
    if not isinstance(deps, dict):
        return []
    records: list[InventoryRecord] = []
    seen: set[str] = set()
    for name in deps.keys():
        if not isinstance(name, str):
            continue
        lower = name.lower()
        if lower in seen or _is_aliased(aliases, name):
            continue
        if _name_keyword_match(name) is None:
            continue
        seen.add(lower)
        records.append(
            _candidate(
                record_id=f"heuristic:npm:{lower}",
                display_name=name,
                kind="cli",
                host="npm",
                confidence="low",
                evidence_kind="npm_global",
                evidence_summary=f"npm_global:name={name}",
            )
        )
    return records


def _scan_pipx(aliases: set[str]) -> list[InventoryRecord]:
    raw = _run_subprocess("pipx", ["list", "--json"])
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    venvs = data.get("venvs") if isinstance(data, dict) else None
    if not isinstance(venvs, dict):
        return []
    records: list[InventoryRecord] = []
    seen: set[str] = set()
    for key in venvs.keys():
        if not isinstance(key, str):
            continue
        lower = key.lower()
        if lower in seen or _is_aliased(aliases, key):
            continue
        if _name_keyword_match(key) is None:
            continue
        seen.add(lower)
        records.append(
            _candidate(
                record_id=f"heuristic:pipx:{lower}",
                display_name=key,
                kind="cli",
                host="pipx",
                confidence="low",
                evidence_kind="pipx",
                evidence_summary=f"pipx:name={key}",
            )
        )
    return records


def _scan_vscode_extensions(aliases: set[str]) -> list[InventoryRecord]:
    ext_dir = Path.home() / ".vscode" / "extensions"
    if not ext_dir.is_dir():
        return []
    try:
        entries = list(ext_dir.iterdir())
    except OSError:
        return []
    records: list[InventoryRecord] = []
    seen: set[str] = set()
    for entry in entries:
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
        except OSError:
            continue
        manifest = entry / "package.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        publisher = data.get("publisher")
        if not isinstance(name, str):
            continue
        full_name = (
            f"{publisher}.{name}" if isinstance(publisher, str) else name
        )
        full_lower = full_name.lower()
        if full_lower in seen:
            continue
        display_name_field = data.get("displayName")
        display_name = (
            display_name_field if isinstance(display_name_field, str) else name
        )
        if _is_aliased(aliases, full_name, name, display_name):
            continue

        evidence: str | None = None
        confidence: Confidence = "low"

        categories = data.get("categories")
        if isinstance(categories, list):
            for c in categories:
                if isinstance(c, str) and c.strip().lower() in VSCODE_AI_CATEGORIES:
                    evidence = f"vscode_extension:categories={c.strip()}"
                    confidence = "medium"
                    break

        if evidence is None:
            keywords_field = data.get("keywords")
            if isinstance(keywords_field, list):
                for kw in keywords_field:
                    if isinstance(kw, str) and kw.strip().lower() in AI_KEYWORDS:
                        evidence = f"vscode_extension:keywords={kw.strip().lower()}"
                        confidence = "medium"
                        break

        if evidence is None:
            matched = _name_keyword_match(name) or _name_keyword_match(display_name)
            if matched is not None:
                evidence = f"vscode_extension:name={matched}"
                confidence = "low"

        if evidence is None:
            continue

        seen.add(full_lower)
        records.append(
            _candidate(
                record_id=f"heuristic:vscode:{full_lower}",
                display_name=display_name,
                kind="extension",
                host="vscode",
                confidence=confidence,
                evidence_kind="vscode_extension_metadata",
                evidence_summary=evidence,
            )
        )
    return records


def _scan_windows_uninstall(aliases: set[str]) -> list[InventoryRecord]:
    if sys.platform != "win32":
        return []
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return []

    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )

    records: list[InventoryRecord] = []
    seen: set[str] = set()
    for hive, sub in roots:
        try:
            parent = winreg.OpenKey(hive, sub)
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(parent, subkey_name) as subkey:
                        try:
                            display_name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                        except OSError:
                            continue
                        try:
                            publisher_value, _ = winreg.QueryValueEx(subkey, "Publisher")
                        except OSError:
                            publisher_value = None
                except OSError:
                    continue
                if not isinstance(display_name, str):
                    continue
                key_lower = display_name.lower()
                if key_lower in seen:
                    continue
                publisher_str = (
                    publisher_value if isinstance(publisher_value, str) else ""
                )
                if _is_aliased(aliases, display_name, publisher_str):
                    continue

                matched = _name_keyword_match(display_name)
                field_name = "DisplayName"
                confidence: Confidence = "low"
                if matched is None and publisher_str:
                    matched = _name_keyword_match(publisher_str)
                    field_name = "Publisher"
                    confidence = "medium"
                if matched is None:
                    continue

                seen.add(key_lower)
                records.append(
                    _candidate(
                        record_id=f"heuristic:winuninstall:{key_lower}",
                        display_name=display_name,
                        kind="app",
                        host="desktop",
                        confidence=confidence,
                        evidence_kind="windows_uninstall_registry",
                        evidence_summary=f"windows_uninstall:{field_name}={matched}",
                    )
                )
        finally:
            try:
                parent.Close()
            except OSError:
                pass
    return records


def _scan_macos_apps(aliases: set[str]) -> list[InventoryRecord]:  # noqa: ARG001
    return []


def _scan_linux_desktop_files(aliases: set[str]) -> list[InventoryRecord]:  # noqa: ARG001
    return []


def _scan_mcp_shaped_json(aliases: set[str]) -> list[InventoryRecord]:
    records: list[InventoryRecord] = []
    seen: set[str] = set()
    for path, server_name in iter_mcp_server_names():
        lower = server_name.lower()
        if lower in seen or _is_aliased(aliases, server_name):
            continue
        seen.add(lower)
        records.append(
            _candidate(
                record_id=f"heuristic:mcp:{lower}",
                display_name=server_name,
                kind="connector",
                host="mcp",
                confidence="medium",
                evidence_kind="mcp_shaped_json",
                evidence_summary=f"mcp:{path.name}:server={server_name}",
            )
        )
    return records


def run_heuristics(aliases: set[str]) -> list[InventoryRecord]:
    aliases_lower = {a.lower() for a in aliases}
    records: list[InventoryRecord] = []
    records.extend(_scan_path(aliases_lower))
    records.extend(_scan_npm_global(aliases_lower))
    records.extend(_scan_pipx(aliases_lower))
    records.extend(_scan_vscode_extensions(aliases_lower))
    records.extend(_scan_windows_uninstall(aliases_lower))
    records.extend(_scan_macos_apps(aliases_lower))
    records.extend(_scan_linux_desktop_files(aliases_lower))
    records.extend(_scan_mcp_shaped_json(aliases_lower))
    return records


__all__ = ["run_heuristics", "AI_KEYWORDS"]
