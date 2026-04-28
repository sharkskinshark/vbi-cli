"""Tier 1: registered descriptors and safe detection rules.

Detection rules read only public-shaped surfaces: PATH, VS Code extension
directory names, user-scoped directory existence, and top-level mcpServers
keys in whitelisted JSON files. No file content beyond declared metadata is
read. No subprocess invocation here.

Tools that require reading credential payloads, OAuth files, or secret-bearing
manifests (Office WEF add-in manifests, Revit addin XML, browser session
stores) are intentionally out of scope until safe rules land. See the
"Deferred Detection" section of docs/INVENTORY_CONTRACT.md.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from vbi.registry import adapter_status_for_record

from .mcp_utils import iter_mcp_server_names
from .records import (
    Confidence,
    Host,
    InventoryRecord,
    InventoryStatus,
    Kind,
    UsageStatus,
    utc_now_iso,
)


DetectionRule = Callable[[], "tuple[InventoryStatus, str, str | None] | None"]


@dataclass(frozen=True)
class Descriptor:
    record_id: str
    display_name: str
    kind: Kind
    host: Host
    usage_status_default: UsageStatus
    detection_rules: tuple[DetectionRule, ...]
    dedup_aliases: tuple[str, ...] = field(default_factory=tuple)
    notes: str | None = None


def _path_command(name: str) -> DetectionRule:
    def rule() -> "tuple[InventoryStatus, str, str | None] | None":
        if shutil.which(name) is not None:
            return ("confirmed", "path_command", f"{name} on PATH")
        return None

    return rule


def _vscode_extension(extension_id: str) -> DetectionRule:
    def rule() -> "tuple[InventoryStatus, str, str | None] | None":
        ext_dir = Path.home() / ".vscode" / "extensions"
        if not ext_dir.is_dir():
            return None
        prefix = extension_id.lower() + "-"
        try:
            for entry in ext_dir.iterdir():
                if entry.is_dir() and entry.name.lower().startswith(prefix):
                    return (
                        "confirmed",
                        "vscode_extension_metadata",
                        f"{extension_id} extension directory present",
                    )
        except OSError:
            return None
        return None

    return rule


def _user_directory(label: str, *parts: str) -> DetectionRule:
    """Detect existence of a known user-scoped directory.

    The probe never reads files inside; it only checks ``is_dir``.
    """

    def rule() -> "tuple[InventoryStatus, str, str | None] | None":
        target = Path.home().joinpath(*parts)
        if target.is_dir():
            return ("found", "app_directory", f"{label} present")
        return None

    return rule


def _mcp_server_present(*server_names: str) -> DetectionRule:
    """Detect a known MCP server by name in any whitelist root.

    Reads only top-level keys of the ``mcpServers`` dict. Values are never
    read or returned.
    """

    targets = {n.lower() for n in server_names}

    def rule() -> "tuple[InventoryStatus, str, str | None] | None":
        for path, server_name in iter_mcp_server_names():
            if server_name.lower() in targets:
                return (
                    "configured",
                    "mcp_config_entry",
                    f"mcp server '{server_name}' in {path.name}",
                )
        return None

    return rule


def _office_wef_addin(*keywords: str) -> DetectionRule:
    """Detect a Microsoft Office web add-in by manifest keyword presence.

    Office add-in manifests are documented public schema (no secrets). The
    rule walks ``%LOCALAPPDATA%\\Microsoft\\Office\\16.0\\Wef`` for ``*.xml``
    files with bounded depth and per-file size, performs a substring check,
    and reports only the matched keyword. No field values are extracted.
    """

    targets = tuple(k.lower() for k in keywords)
    MAX_DEPTH = 6
    MAX_BYTES = 100_000

    def rule() -> "tuple[InventoryStatus, str, str | None] | None":
        wef = (
            Path.home()
            / "AppData"
            / "Local"
            / "Microsoft"
            / "Office"
            / "16.0"
            / "Wef"
        )
        if not wef.is_dir():
            return None
        try:
            for item in wef.rglob("*.xml"):
                try:
                    rel_parts = item.relative_to(wef).parts
                except ValueError:
                    continue
                if len(rel_parts) > MAX_DEPTH:
                    continue
                try:
                    if item.stat().st_size > MAX_BYTES:
                        continue
                    text = item.read_text(encoding="utf-8", errors="ignore").lower()
                except OSError:
                    continue
                for keyword in targets:
                    if keyword in text:
                        return (
                            "found",
                            "office_wef_manifest",
                            f"Office add-in manifest mentions '{keyword}'",
                        )
        except OSError:
            return None
        return None

    return rule


DESCRIPTORS: tuple[Descriptor, ...] = (
    # ============================================================
    # Core IDEs / shells
    # ============================================================
    Descriptor(
        record_id="vscode",
        display_name="Visual Studio Code",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _path_command("code"),
            _user_directory("VS Code extensions root", ".vscode", "extensions"),
        ),
        dedup_aliases=("code", "vscode"),
    ),
    Descriptor(
        record_id="cursor",
        display_name="Cursor",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _path_command("cursor"),
            _user_directory("Cursor user dir", "AppData", "Roaming", "Cursor"),
            _user_directory("Cursor user dir (macOS)", "Library", "Application Support", "Cursor"),
        ),
        dedup_aliases=("cursor",),
    ),
    Descriptor(
        record_id="windsurf",
        display_name="Windsurf",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _path_command("windsurf"),
            _user_directory("Windsurf user dir", "AppData", "Roaming", "Windsurf"),
            _user_directory("Windsurf user dir (macOS)", "Library", "Application Support", "Windsurf"),
        ),
        dedup_aliases=("windsurf",),
    ),
    Descriptor(
        record_id="antigravity",
        display_name="Antigravity",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("Antigravity user dir", ".antigravity"),
        ),
        dedup_aliases=("antigravity",),
    ),

    # ============================================================
    # AI coding assistants and chat clients
    # ============================================================
    Descriptor(
        record_id="claude-code-cli",
        display_name="Claude Code CLI",
        kind="cli",
        host="terminal",
        usage_status_default="telemetry_possible",
        detection_rules=(_path_command("claude"),),
        dedup_aliases=("claude", "claude-code"),
    ),
    Descriptor(
        record_id="claude-desktop",
        display_name="Claude Desktop",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("Claude desktop user dir (Windows)", "AppData", "Roaming", "Claude"),
            _user_directory("Claude desktop user dir (macOS)", "Library", "Application Support", "Claude"),
        ),
        dedup_aliases=("claude-desktop",),
    ),
    Descriptor(
        record_id="codex-cli",
        display_name="Codex CLI",
        kind="cli",
        host="terminal",
        usage_status_default="telemetry_possible",
        detection_rules=(_path_command("codex"),),
        dedup_aliases=("codex",),
    ),
    Descriptor(
        record_id="gemini-cli",
        display_name="Gemini CLI",
        kind="cli",
        host="terminal",
        usage_status_default="official_api_possible",
        detection_rules=(_path_command("gemini"),),
        dedup_aliases=("gemini",),
    ),
    Descriptor(
        record_id="aider",
        display_name="Aider",
        kind="cli",
        host="terminal",
        usage_status_default="telemetry_possible",
        detection_rules=(_path_command("aider"),),
        dedup_aliases=("aider", "aider-chat"),
    ),
    Descriptor(
        record_id="openai-cli",
        display_name="OpenAI CLI",
        kind="cli",
        host="terminal",
        usage_status_default="official_api_possible",
        detection_rules=(_path_command("openai"),),
        dedup_aliases=("openai",),
    ),
    Descriptor(
        record_id="github-cli",
        display_name="GitHub CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("gh"),),
        dedup_aliases=("gh",),
    ),
    Descriptor(
        record_id="github-copilot-vscode",
        display_name="GitHub Copilot (VS Code)",
        kind="extension",
        host="vscode",
        usage_status_default="policy_only",
        detection_rules=(_vscode_extension("github.copilot"),),
        dedup_aliases=("github.copilot", "copilot"),
    ),
    Descriptor(
        record_id="github-copilot-chat",
        display_name="GitHub Copilot Chat (VS Code)",
        kind="extension",
        host="vscode",
        usage_status_default="policy_only",
        detection_rules=(_vscode_extension("github.copilot-chat"),),
        dedup_aliases=("github.copilot-chat",),
    ),
    Descriptor(
        record_id="continue-vscode",
        display_name="Continue (VS Code)",
        kind="extension",
        host="vscode",
        usage_status_default="policy_only",
        detection_rules=(_vscode_extension("Continue.continue"),),
        dedup_aliases=("continue.continue", "continue"),
    ),
    Descriptor(
        record_id="codeium-vscode",
        display_name="Codeium (VS Code)",
        kind="extension",
        host="vscode",
        usage_status_default="policy_only",
        detection_rules=(_vscode_extension("Codeium.codeium"),),
        dedup_aliases=("codeium.codeium", "codeium"),
    ),
    Descriptor(
        record_id="tabnine-vscode",
        display_name="Tabnine (VS Code)",
        kind="extension",
        host="vscode",
        usage_status_default="policy_only",
        detection_rules=(_vscode_extension("TabNine.tabnine-vscode"),),
        dedup_aliases=("tabnine.tabnine-vscode", "tabnine"),
    ),
    Descriptor(
        record_id="gemini-code-assist",
        display_name="Gemini Code Assist (VS Code)",
        kind="extension",
        host="vscode",
        usage_status_default="policy_only",
        detection_rules=(_vscode_extension("Google.geminicodeassist"),),
        dedup_aliases=("google.geminicodeassist", "gemini-code-assist"),
    ),

    # ============================================================
    # Microsoft Office add-ins (WEF cache)
    # ============================================================
    Descriptor(
        record_id="claude-for-excel",
        display_name="Claude for Excel",
        kind="extension",
        host="system",
        usage_status_default="policy_only",
        detection_rules=(_office_wef_addin("claude for excel", "claude-for-excel"),),
        dedup_aliases=("claude-for-excel",),
    ),
    Descriptor(
        record_id="claude-for-word",
        display_name="Claude for Word",
        kind="extension",
        host="system",
        usage_status_default="policy_only",
        detection_rules=(_office_wef_addin("claude for word", "claude-for-word"),),
        dedup_aliases=("claude-for-word",),
    ),
    Descriptor(
        record_id="claude-for-ppt",
        display_name="Claude for PowerPoint",
        kind="extension",
        host="system",
        usage_status_default="policy_only",
        detection_rules=(_office_wef_addin("claude for powerpoint", "claude-for-ppt", "claude-for-powerpoint"),),
        dedup_aliases=("claude-for-ppt", "claude-for-powerpoint"),
    ),

    # ============================================================
    # Open-source agent runtimes and frameworks
    # ============================================================
    Descriptor(
        record_id="opencode",
        display_name="OpenCode",
        kind="cli",
        host="terminal",
        usage_status_default="telemetry_possible",
        detection_rules=(
            _path_command("opencode"),
            _user_directory("OpenCode user dir (XDG)", ".local", "share", "opencode"),
            _user_directory("OpenCode user dir (legacy)", ".opencode"),
        ),
        dedup_aliases=("opencode",),
    ),
    Descriptor(
        record_id="openclaw",
        display_name="OpenClaw",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("OpenClaw skills dir", ".openclaw", "skills"),
            _user_directory("OpenClaw user dir", ".openclaw"),
        ),
        dedup_aliases=("openclaw",),
    ),
    Descriptor(
        record_id="hermes-agent",
        display_name="Hermes Agent",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("Hermes user dir", ".hermes"),),
        dedup_aliases=("hermes", "hermes-agent"),
    ),
    Descriptor(
        record_id="oscopilot",
        display_name="OS-Copilot",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("OS-Copilot user dir", ".oscopilot"),),
        dedup_aliases=("oscopilot",),
    ),
    Descriptor(
        record_id="superagi",
        display_name="SuperAGI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("SuperAGI user dir", ".superagi"),),
        dedup_aliases=("superagi",),
    ),
    Descriptor(
        record_id="devika",
        display_name="Devika",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("Devika user dir", ".devika"),),
        dedup_aliases=("devika",),
    ),
    Descriptor(
        record_id="taskweaver",
        display_name="TaskWeaver",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("TaskWeaver user dir", ".taskweaver"),),
        dedup_aliases=("taskweaver",),
    ),
    Descriptor(
        record_id="autogenstudio",
        display_name="AutoGen Studio",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("AutoGen Studio user dir", ".autogenstudio"),),
        dedup_aliases=("autogenstudio",),
    ),
    Descriptor(
        record_id="openhands",
        display_name="OpenHands",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_user_directory("OpenHands user dir", ".openhands"),),
        dedup_aliases=("openhands",),
    ),
    Descriptor(
        record_id="crewai",
        display_name="CrewAI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("crewai"),),
        dedup_aliases=("crewai",),
    ),

    # ============================================================
    # Local model runners and chat UIs
    # ============================================================
    Descriptor(
        record_id="ollama",
        display_name="Ollama",
        kind="cli",
        host="terminal",
        usage_status_default="telemetry_possible",
        detection_rules=(
            _path_command("ollama"),
            _user_directory("Ollama user dir", ".ollama"),
        ),
        dedup_aliases=("ollama",),
    ),
    Descriptor(
        record_id="lm-studio",
        display_name="LM Studio",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("LM Studio user dir (Windows)", "AppData", "Roaming", "LM Studio"),
            _user_directory("LM Studio user dir (macOS)", "Library", "Application Support", "LM Studio"),
            _user_directory("LM Studio cache (Linux)", ".cache", "lm-studio"),
        ),
        dedup_aliases=("lm-studio", "lmstudio"),
    ),
    Descriptor(
        record_id="chatgpt-desktop",
        display_name="ChatGPT Desktop",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("ChatGPT desktop dir (Windows)", "AppData", "Roaming", "ChatGPT"),
            _user_directory("ChatGPT desktop dir (Windows alt)", "AppData", "Roaming", "OpenAI", "ChatGPT"),
            _user_directory("ChatGPT desktop dir (macOS)", "Library", "Application Support", "ChatGPT"),
            _user_directory("ChatGPT desktop dir (macOS bundle id)", "Library", "Application Support", "com.openai.chat"),
        ),
        dedup_aliases=("chatgpt-desktop", "chatgpt"),
    ),
    Descriptor(
        record_id="gpt4all",
        display_name="GPT4All",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("GPT4All dir (Windows)", "AppData", "Roaming", "nomic.ai", "GPT4All"),
            _user_directory("GPT4All dir (POSIX)", ".gpt4all"),
            _path_command("gpt4all"),
        ),
        dedup_aliases=("gpt4all",),
    ),
    Descriptor(
        record_id="vllm",
        display_name="vLLM",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("vllm"),),
        dedup_aliases=("vllm",),
    ),
    Descriptor(
        record_id="anythingllm",
        display_name="AnythingLLM",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("AnythingLLM dir (Windows)", "AppData", "Roaming", "anythingllm-desktop"),
            _user_directory("AnythingLLM dir (POSIX)", ".anythingllm"),
        ),
        dedup_aliases=("anythingllm",),
    ),

    # ============================================================
    # MLOps, profiling, and notebooks
    # ============================================================
    Descriptor(
        record_id="wandb",
        display_name="Weights & Biases",
        kind="cli",
        host="terminal",
        usage_status_default="official_api_possible",
        detection_rules=(_path_command("wandb"),),
        dedup_aliases=("wandb",),
    ),
    Descriptor(
        record_id="mlflow",
        display_name="MLflow",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("mlflow"),),
        dedup_aliases=("mlflow",),
    ),
    Descriptor(
        record_id="jupyter",
        display_name="Jupyter",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("jupyter"),),
        dedup_aliases=("jupyter",),
    ),
    Descriptor(
        record_id="conda",
        display_name="Conda",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("conda"),),
        dedup_aliases=("conda",),
    ),
    Descriptor(
        record_id="nvtop",
        display_name="nvtop",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("nvtop"),),
        dedup_aliases=("nvtop",),
    ),

    # ============================================================
    # Cloud and platform CLIs
    # ============================================================
    Descriptor(
        record_id="vercel-cli",
        display_name="Vercel CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("vercel"),),
        dedup_aliases=("vercel",),
    ),
    Descriptor(
        record_id="huggingface-cli",
        display_name="Hugging Face CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("hf"),),
        dedup_aliases=("hf", "huggingface", "huggingface-cli"),
    ),
    Descriptor(
        record_id="gcloud",
        display_name="Google Cloud SDK",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("gcloud"),),
        dedup_aliases=("gcloud",),
    ),
    Descriptor(
        record_id="bq",
        display_name="BigQuery CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("bq"),),
        dedup_aliases=("bq",),
    ),
    Descriptor(
        record_id="firebase-cli",
        display_name="Firebase CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(_path_command("firebase"),),
        dedup_aliases=("firebase", "firebase-tools"),
    ),
    Descriptor(
        record_id="supabase-cli",
        display_name="Supabase CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(
            _path_command("supabase"),
            _user_directory("Supabase user dir", ".supabase"),
        ),
        dedup_aliases=("supabase",),
    ),
    Descriptor(
        record_id="zeabur-cli",
        display_name="Zeabur CLI",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(
            _path_command("zeabur"),
            _user_directory("Zeabur user dir", ".zeabur"),
        ),
        dedup_aliases=("zeabur",),
    ),
    Descriptor(
        record_id="google-drive-desktop",
        display_name="Google Drive Desktop",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("Google DriveFS dir (Windows)", "AppData", "Local", "Google", "DriveFS"),
            _user_directory("Google Drive cache (macOS)", "Library", "Application Support", "Google", "DriveFS"),
        ),
        dedup_aliases=("google-drive", "drivefs"),
    ),

    # ============================================================
    # OS productivity and automation
    # ============================================================
    Descriptor(
        record_id="raycast",
        display_name="Raycast",
        kind="app",
        host="desktop",
        usage_status_default="policy_only",
        detection_rules=(
            _user_directory("Raycast user dir (macOS)", "Library", "Application Support", "com.raycast.macos"),
        ),
        dedup_aliases=("raycast",),
    ),
    Descriptor(
        record_id="powertoys",
        display_name="Microsoft PowerToys",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("PowerToys user dir", "AppData", "Local", "Microsoft", "PowerToys"),
        ),
        dedup_aliases=("powertoys",),
    ),
    Descriptor(
        record_id="n8n",
        display_name="n8n",
        kind="cli",
        host="terminal",
        usage_status_default="unavailable",
        detection_rules=(
            _path_command("n8n"),
            _user_directory("n8n user dir", ".n8n"),
        ),
        dedup_aliases=("n8n",),
    ),

    # ============================================================
    # Productivity and design (apps + MCP connectors)
    # ============================================================
    Descriptor(
        record_id="obsidian",
        display_name="Obsidian",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("Obsidian config (Windows)", "AppData", "Roaming", "obsidian"),
            _user_directory("Obsidian config (Linux)", ".config", "obsidian"),
            _user_directory("Obsidian config (macOS)", "Library", "Application Support", "obsidian"),
        ),
        dedup_aliases=("obsidian",),
    ),
    Descriptor(
        record_id="logseq",
        display_name="Logseq",
        kind="app",
        host="desktop",
        usage_status_default="unavailable",
        detection_rules=(
            _user_directory("Logseq config (Windows)", "AppData", "Roaming", "Logseq"),
            _user_directory("Logseq config (Linux)", ".config", "Logseq"),
            _user_directory("Logseq config (macOS)", "Library", "Application Support", "Logseq"),
        ),
        dedup_aliases=("logseq",),
    ),
    Descriptor(
        record_id="figma-mcp",
        display_name="Figma MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("figma"),),
        dedup_aliases=("figma",),
    ),
    Descriptor(
        record_id="miro-mcp",
        display_name="Miro MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("miro"),),
        dedup_aliases=("miro",),
    ),
    Descriptor(
        record_id="notion-mcp",
        display_name="Notion MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("notion"),),
        dedup_aliases=("notion",),
    ),
    Descriptor(
        record_id="canva-mcp",
        display_name="Canva MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("canva"),),
        dedup_aliases=("canva",),
    ),
    Descriptor(
        record_id="google-workspace-mcp",
        display_name="Google Workspace MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("google-workspace", "google_workspace", "googleworkspace"),),
        dedup_aliases=("google-workspace", "google_workspace"),
    ),
    Descriptor(
        record_id="make-mcp",
        display_name="Make MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("make", "make.com"),),
        dedup_aliases=("make-mcp",),
    ),
    Descriptor(
        record_id="lovart-mcp",
        display_name="Lovart MCP Connector",
        kind="connector",
        host="mcp",
        usage_status_default="unavailable",
        detection_rules=(_mcp_server_present("lovart"),),
        dedup_aliases=("lovart",),
    ),

    # AEC tools (Grasshopper, RhinoCode, Revit-MCP, etc.) are intentionally
    # excluded; see docs/OUT_OF_SCOPE.md.
)


def _build_record(
    descriptor: Descriptor,
    *,
    inventory_status: InventoryStatus,
    confidence: Confidence,
    evidence_kind: str,
    evidence_summary: str | None = None,
    blocked_reason: str | None = None,
) -> InventoryRecord:
    return InventoryRecord(
        record_id=descriptor.record_id,
        display_name=descriptor.display_name,
        kind=descriptor.kind,
        host=descriptor.host,
        tier="registry",
        inventory_status=inventory_status,
        confidence=confidence,
        usage_status=descriptor.usage_status_default,
        detected_at=utc_now_iso(),
        evidence_kind=evidence_kind,
        adapter_status=adapter_status_for_record(descriptor.record_id),
        evidence_summary=evidence_summary,
        blocked_reason=blocked_reason,
        notes=descriptor.notes,
    )


def _scan_descriptor(descriptor: Descriptor) -> InventoryRecord:
    if not descriptor.detection_rules:
        return _build_record(
            descriptor,
            inventory_status="missing",
            confidence="unknown",
            evidence_kind="none",
            blocked_reason="no safe detection rule implemented for this descriptor",
        )

    for rule in descriptor.detection_rules:
        try:
            outcome = rule()
        except Exception as exc:  # noqa: BLE001 - one rule must not stop the scan
            return _build_record(
                descriptor,
                inventory_status="unknown",
                confidence="unknown",
                evidence_kind="error",
                blocked_reason=f"detection rule raised {type(exc).__name__}",
            )
        if outcome is not None:
            status, evidence_kind, summary = outcome
            return _build_record(
                descriptor,
                inventory_status=status,
                confidence="high",
                evidence_kind=evidence_kind,
                evidence_summary=summary,
            )

    return _build_record(
        descriptor,
        inventory_status="missing",
        confidence="high",
        evidence_kind="none",
    )


def scan_registry() -> list[InventoryRecord]:
    return [_scan_descriptor(d) for d in DESCRIPTORS]


def all_aliases() -> set[str]:
    aliases: set[str] = set()
    for d in DESCRIPTORS:
        for alias in d.dedup_aliases:
            aliases.add(alias.lower())
    return aliases
