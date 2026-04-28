"""Minimal VBI CLI entry point for the release skeleton."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audit import has_critical, render_findings, run_audit
from .dashboard import run_dashboard
from .inventory import fetch_cached_status, render_inventory, run_inventory
from .live import run_live
from .map_cmd import run_map
from .registry import get_adapters
from .update_cmd import run_update


COMMANDS = [
    "doctor",
    "init",
    "sync",
    "status",
    "inventory",
    "dashboard",
    "live",
    "map",
    "update",
    "audit",
    "export",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vbi",
        description="Local-first AI usage inspection CLI."
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("doctor", help="inspect local readiness")
    subparsers.add_parser("init", help="(not yet implemented) initialize VBI-owned local config")

    sync_parser = subparsers.add_parser("sync", help="refresh stale or missing provider records")
    sync_parser.add_argument("--provider", default="all", help="provider record_id or 'all'")
    sync_parser.add_argument("--force", action="store_true", help="refresh even when cache is fresh")

    status_parser = subparsers.add_parser("status", help="show current cached/degraded provider status")
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

    subparsers.add_parser("audit", help="run release safety audit")
    subparsers.add_parser("export", help="(not yet implemented) export sanitized report")

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

    return parser


def _run_doctor() -> int:
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
        cached = adapter.read_cache()
        if cached is None:
            result = adapter.sync(force=False)
            record = result.record
        else:
            record = cached
        if record is None:
            print(f"{adapter.record_id} | unavailable | unknown | no record")
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
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
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
            return _run_doctor()
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
        if args.command == "update":
            return run_update(check_only=args.check)
    except KeyboardInterrupt:
        # Child process interrupted — exit with 130 (Ctrl+C convention) so
        # the parent home REPL can detect this as an interrupt and arm its
        # double-tap exit window.
        return 130

    print(f"vbi {args.command}: not yet implemented in this release")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


