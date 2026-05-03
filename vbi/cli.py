"""Minimal VBI CLI entry point for the release skeleton."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .audit import has_critical, render_findings, run_audit
from .dashboard import run_dashboard
from .export_cmd import run_export
from .inventory import fetch_cached_status, render_inventory, run_inventory
from .live import run_live
from .map_cmd import run_map
from .registry import get_adapters
from .runtime_cmd import run_cleanup, run_runtime_scan
from .update_cmd import run_update


COMMANDS = [
    "doctor",
    "init",
    "sync",
    "status",
    "inventory",
    "dashboard",
    "live",
    "cleanup",
    "map",
    "update",
    "audit",
    "export",
]


def _configure_windows_console() -> None:
    """Enable UTF-8 and ANSI processing for Windows console hosts."""
    if sys.platform != "win32":
        return

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    vt_enabled = False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        enable_vt = 0x0004
        handles = (-11, -12)  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
        for handle_id in handles:
            handle = kernel32.GetStdHandle(handle_id)
            if handle == ctypes.c_void_p(-1).value:
                continue
            mode = ctypes.c_uint()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                continue
            if kernel32.SetConsoleMode(handle, mode.value | enable_vt):
                vt_enabled = True
    except Exception:  # noqa: BLE001
        vt_enabled = False

    # If the host cannot process ANSI, force all VBI renderers into their
    # existing plain-text path instead of leaking raw escape sequences.
    if sys.stdout.isatty() and not vt_enabled:
        os.environ.setdefault("NO_COLOR", "1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vbi",
        description="Local-first AI usage inspection CLI."
    )
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser("doctor", help="inspect local readiness")
    doctor_parser.add_argument(
        "topic",
        nargs="?",
        choices=["readiness", "runtime"],
        default="readiness",
        help="inspection topic (default: readiness)",
    )
    doctor_parser.add_argument(
        "--all",
        action="store_true",
        help="for `runtime`: show all relevant runtime processes, not only duplicates",
    )
    subparsers.add_parser("init", help="(not yet implemented) initialize VBI-owned local config")

    sync_parser = subparsers.add_parser("sync", help="refresh stale or missing provider records")
    sync_parser.add_argument("--provider", default="all", help="provider record_id or 'all'")
    sync_parser.add_argument("--force", action="store_true", help="refresh even when cache is fresh")

    status_parser = subparsers.add_parser("status", help="show cached provider status without syncing")
    status_parser.add_argument("--json", action="store_true", help="reserved for future JSON output")

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="discover local AI tooling footprints (read-only, no credentials)",
    )
    inventory_parser.add_argument(
        "--heuristics",
        action="store_true",
        help="enable Tier 2 generic discovery (PATH, VS Code, npm/pipx, OS apps, MCP-shaped JSON)",
    )
    inventory_parser.add_argument(
        "--with-status",
        action="store_true",
        help="enrich Tier 1 with cached usage from any registered provider adapter (cache-only, no sync)",
    )

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="resident terminal dashboard of providers that report cached usage",
    )
    dashboard_parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="refresh interval in seconds (default: 30, minimum: 5)",
    )
    dashboard_parser.add_argument(
        "--once",
        action="store_true",
        help="render once and exit (useful for scripting and tests)",
    )

    live_parser = subparsers.add_parser(
        "live",
        help="real-time bar-chart view: syncs on every refresh, shows usage + reset countdown bars",
    )
    live_parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="refresh interval in seconds (default: 30, minimum: 10)",
    )
    live_parser.add_argument(
        "--once",
        action="store_true",
        help="render once and exit",
    )

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="scan duplicate MCP / Node / Python runtime processes (dry-run by default)",
    )
    cleanup_parser.add_argument(
        "--all",
        action="store_true",
        help="show all relevant runtime processes, not only duplicates",
    )
    cleanup_parser.add_argument(
        "--apply",
        action="store_true",
        help="stop older duplicates, keeping the newest in each group (prompts unless --yes)",
    )
    cleanup_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip the confirmation prompt when used with --apply",
    )
    cleanup_parser.add_argument(
        "--groups",
        metavar="PATTERNS",
        default=None,
        help=(
            "comma-separated signatures or fnmatch globs (e.g. 'mcp:*' or "
            "'mcp:google-workspace-mcp,mcp:mcp'); only matching duplicate "
            "groups are targeted by --apply"
        ),
    )

    subparsers.add_parser("audit", help="run GitHub release safety audit")

    export_parser = subparsers.add_parser(
        "export",
        help="write a sanitized JSON report (inventory + cached usage + audit) to ~/vbi-report-YYYYMMDD.json",
    )
    export_parser.add_argument(
        "--output",
        default=None,
        help="override the output path (default: ~/vbi-report-YYYYMMDD.json)",
    )

    update_parser = subparsers.add_parser(
        "update",
        help="check for newer source and refresh the editable install (git pull + pip install -e .)",
    )
    update_parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether an update is available; do not pull or reinstall",
    )

    map_parser = subparsers.add_parser(
        "map",
        help="show detected AI tooling as a host-first hierarchy (default: colored tree in terminal)",
    )
    map_parser.add_argument(
        "--mermaid",
        action="store_true",
        help="emit Mermaid source instead of the terminal tree (for GitHub/Notion embedding)",
    )
    map_parser.add_argument(
        "--html",
        action="store_true",
        help="emit a full HTML page with the Mermaid graph rendered (open in any browser)",
    )
    map_parser.add_argument(
        "--output",
        default=None,
        help="write to a file instead of stdout",
    )

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="run vbi as an MCP server for LLM clients (Claude Code, Claude Desktop)",
    )
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_cmd", required=True)
    serve_parser = mcp_sub.add_parser(
        "serve",
        help="start the vbi MCP server (default transport: stdio)",
    )
    serve_parser.add_argument(
        "--transport",
        choices=["stdio"],
        default="stdio",
        help="MCP transport (only stdio supported in this release)",
    )
    install_parser = mcp_sub.add_parser(
        "install",
        help="register vbi as an MCP server with Claude Code (auto-detects config)",
    )
    install_parser.add_argument(
        "--config",
        default=None,
        help="override config path (default: auto-detect Claude Code / Claude Desktop)",
    )
    install_parser.add_argument(
        "--name",
        default="vbi",
        help="MCP server name to register under mcpServers (default: vbi)",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing entry without prompting",
    )

    return parser


def _run_doctor(topic: str = "readiness", show_all: bool = False) -> int:
    if topic == "runtime":
        return run_runtime_scan(show_all=show_all)

    adapters = get_adapters()
    print("VBI doctor: release skeleton")
    print(f"Provider adapters registered: {len(adapters)}")
    for adapter in adapters:
        availability = adapter.detect()
        state = "installed" if availability.installed else "unavailable"
        print(f"- {availability.record_id}: {state} ({availability.blocked_reason})")
    return 0


def _run_status() -> int:
    print("record_id | source_type | confidence | status")
    for adapter in get_adapters():
        record = adapter.read_cache()
        if record is None:
            print(f"{adapter.record_id} | unavailable | unknown | no cached record")
        else:
            print(f"{record.record_id} | {record.source_type} | {record.confidence} | {record.blocked_reason or 'ok'}")
    return 0


def _run_sync(force: bool, provider: str) -> int:
    adapters = get_adapters()
    if provider != "all":
        adapters = [adapter for adapter in adapters if getattr(adapter, "record_id", None) == provider]
        if not adapters:
            print(f"No provider adapter found: {provider}")
            return 2

    use_color = sys.stdout.isatty()
    name_width = max(len(getattr(a, "record_id", "")) for a in adapters)

    def _icon(status: str) -> str:
        if not use_color:
            return {"updated": "[ok]", "unavailable": "[--]", "failed": "[!!]"}.get(status, "[??]")
        return {
            "updated":     "\033[32m✓\033[0m",
            "unavailable": "\033[2m·\033[0m",
            "failed":      "\033[91m✗\033[0m",
        }.get(status, "?")

    def _msg(text: str, max_len: int = 78) -> str:
        text = text.replace("\n", " ").strip()
        if len(text) > max_len:
            text = text[: max_len - 1] + "…"
        return f"\033[2m{text}\033[0m" if use_color else text

    exit_code = 0
    for adapter in adapters:
        result = adapter.sync(force=force)
        print(f"  {_icon(result.status)} {result.record_id.ljust(name_width)}  {_msg(result.message)}")
        if result.status == "failed":
            exit_code = 1
    return exit_code


def main() -> int:
    _configure_windows_console()
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        from .splash import splash_sync
        from ._farewell import CtrlCExit
        try:
            splash_sync()
        except KeyboardInterrupt:
            pass  # Ctrl+C during splash → skip to home, do not exit
        try:
            CtrlCExit().handle_interrupt()
        except KeyboardInterrupt:
            pass
        return 0

    try:
        if args.command == "audit":
            root = Path(__file__).resolve().parent.parent
            findings = run_audit(root)
            print(render_findings(findings))
            return 4 if has_critical(findings) else 0
        if args.command == "doctor":
            return _run_doctor(topic=args.topic, show_all=args.all)
        if args.command == "cleanup":
            return run_cleanup(
                show_all=args.all,
                apply=args.apply,
                assume_yes=args.yes,
                groups=args.groups,
            )
        if args.command == "status":
            return _run_status()
        if args.command == "sync":
            return _run_sync(force=args.force, provider=args.provider)
        if args.command == "inventory":
            tier1, tier2 = run_inventory(include_heuristics=args.heuristics)
            status_map = fetch_cached_status(tier1) if args.with_status else None
            print(render_inventory(tier1, tier2, status_map))
            return 0
        if args.command == "dashboard":
            return run_dashboard(interval=args.interval, once=args.once)
        if args.command == "live":
            return run_live(interval=args.interval, once=args.once)
        if args.command == "map":
            return run_map(mermaid=args.mermaid, html=args.html, output=args.output)
        if args.command == "export":
            return run_export(output=args.output)
        if args.command == "update":
            return run_update(check_only=args.check)
        if args.command == "mcp":
            if args.mcp_cmd == "serve":
                from .mcp.server import serve as mcp_serve
                mcp_serve(transport=args.transport)
                return 0
            if args.mcp_cmd == "install":
                from .mcp.install import run_install
                config_path = Path(args.config).expanduser().resolve() if args.config else None
                return run_install(config_path=config_path, name=args.name, force=args.force)
    except KeyboardInterrupt:
        # Child process interrupted — exit with 130 (Ctrl+C convention) so
        # the parent home REPL can detect this as an interrupt and arm its
        # double-tap exit window.
        return 130

    print(f"vbi {args.command}: not yet implemented in this release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
