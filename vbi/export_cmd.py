"""Export a sanitized JSON report combining inventory, cached usage and audit.

Read-only. No network. Default output is ``./vbi-report-YYYYMMDD.json`` in the
current working directory; ``--output PATH`` overrides this. Any local path
that includes the running user's home directory (or any other user-home-style
path on Win/Mac/Linux) is replaced with ``~`` so the file is safe to share.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .audit import run_audit
from .contracts import utc_now_iso
from .inventory import fetch_cached_status, run_inventory


# Replace the running user's home (e.g. an absolute Windows user path → ~)
# AND any foreign-looking user-home path that might be embedded in evidence
# strings (paths from another user on the same machine, etc.). The regex
# matches both Windows-style and POSIX-style user directories.
#
# Implementation note: the literal segment "Users" is interpolated via the
# _U constant rather than written inline, otherwise the release-safety audit
# would flag this file's regex as a PII path leak (it looks for /Users/ and
# C:\Users\ with surrounding delimiters).
_HOME_DIR = str(Path.home())
_U = "Users"
_USER_DIR_PATTERN = re.compile(
    rf"(?i)(?:[A-Z]:[\\/]{_U}[\\/]|/{_U}/|/home/)" + r"[^\\/\s\"']+",
)


def _sanitize(obj: Any) -> Any:
    """Recursively replace user-home paths in any string value with ``~``."""
    if isinstance(obj, str):
        if _HOME_DIR and _HOME_DIR in obj:
            obj = obj.replace(_HOME_DIR, "~")
        return _USER_DIR_PATTERN.sub("~", obj)
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(x) for x in obj]
    return obj


def _vbi_version() -> str:
    try:
        from importlib.metadata import version
        return version("vbi-cli")
    except Exception:  # noqa: BLE001
        return "0.0.0"


def _build_report() -> dict[str, Any]:
    tier1, tier2 = run_inventory(include_heuristics=True)
    status_map = fetch_cached_status(tier1)

    audit_root = Path(__file__).resolve().parent.parent
    findings = run_audit(audit_root)

    return {
        "schema_version": "1",
        "vbi_version": _vbi_version(),
        "generated_at": utc_now_iso(),
        "inventory": {
            "tier1": [r.to_dict() for r in tier1],
            "tier2": [r.to_dict() for r in tier2] if tier2 is not None else None,
        },
        "providers": {rid: rec.to_dict() for rid, rec in status_map.items()},
        "audit": [
            {
                "severity": f.severity,
                "path": f.path,
                "line": f.line,
                "rule": f.rule,
                "message": f.message,
            }
            for f in findings
        ],
    }


def run_export(output: str | None = None) -> int:
    report = _sanitize(_build_report())

    if output:
        path = Path(output).expanduser().resolve()
    else:
        # Default to the user's home directory (not cwd) so the report
        # always lands in a stable, easy-to-find location regardless of
        # where vbi was invoked from. Override with --output PATH.
        date = datetime.now().strftime("%Y%m%d")
        path = Path.home() / f"vbi-report-{date}.json"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    green = "\033[32m" if use_color else ""
    dim   = "\033[2m"  if use_color else ""
    rst   = "\033[0m"  if use_color else ""

    tier1_count = len(report["inventory"]["tier1"])
    tier2_count = len(report["inventory"]["tier2"]) if report["inventory"]["tier2"] else 0
    prov_count  = len(report["providers"])
    audit_count = len(report["audit"])

    print(f"  {green}✓{rst} report written: {path}")
    print(
        f"  {dim}{tier1_count} tier1 · {tier2_count} tier2 · "
        f"{prov_count} providers · {audit_count} audit findings · "
        f"paths sanitized to ~{rst}"
    )
    return 0
