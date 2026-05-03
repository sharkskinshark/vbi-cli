"""Runtime process scanner for duplicate MCP / Node / Python workers."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RuntimeProcess:
    pid: int
    name: str
    command: str
    started_at: str
    cpu_seconds: float
    kind: str
    signature: str


def scan_runtime_processes() -> list[RuntimeProcess]:
    rows = _scan_windows_processes() if os.name == "nt" else _scan_posix_processes()
    processes: list[RuntimeProcess] = []
    for row in rows:
        proc = _row_to_process(row)
        if proc is not None and _is_relevant(proc):
            processes.append(proc)
    processes.sort(key=lambda p: (p.kind, p.signature, p.pid))
    return processes


def render_runtime_report(processes: list[RuntimeProcess], *, show_all: bool = False) -> str:
    duplicate_signatures = _duplicate_signatures(processes)
    rows = [
        p for p in processes
        if show_all or p.signature in duplicate_signatures
    ]

    lines: list[str] = ["VBI runtime process scan"]
    lines.append(
        f"Relevant runtimes: {len(processes)} · duplicate groups: {len(duplicate_signatures)}"
    )
    if not rows:
        lines.append("No duplicate MCP / Node / Python runtime processes found.")
        return "\n".join(lines)

    lines.append("")
    lines.append(_format_table(rows, duplicate_signatures))
    if not show_all:
        lines.append("")
        lines.append("Use `vbi cleanup --all` to show all relevant runtime processes.")
    return "\n".join(lines)


def run_runtime_scan(*, show_all: bool = False) -> int:
    processes = scan_runtime_processes()
    print(render_runtime_report(processes, show_all=show_all))
    return 0


def run_cleanup(
    *,
    show_all: bool = False,
    apply: bool = False,
    assume_yes: bool = False,
    groups: str | None = None,
) -> int:
    processes = scan_runtime_processes()
    full_plan = build_cleanup_plan(processes)

    if not apply:
        print("VBI cleanup dry-run")
        print("No processes were stopped.")
        print("")
        print(render_runtime_report(processes, show_all=show_all))
        if full_plan:
            print("")
            print(_render_signature_list(full_plan))
        print("")
        print("Use `vbi cleanup --apply` to stop older duplicates (keeps the newest in each group).")
        print("Use `--groups <patterns>` to target specific signatures (e.g. `--groups 'mcp:*'`).")
        return 0

    print("VBI cleanup --apply")
    if not full_plan:
        print("No duplicate runtime processes to stop.")
        return 0

    plan = filter_plan_by_groups(full_plan, groups)
    if not plan:
        print(f"No duplicate groups matched filter: {groups!r}")
        print("")
        print(_render_signature_list(full_plan))
        return 1

    print(_render_cleanup_plan(plan))
    if not assume_yes:
        try:
            answer = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("Aborted. No processes were stopped.")
            return 1

    stopped = 0
    failed = 0
    for entry in plan:
        for victim in entry["kill"]:
            ok, msg = terminate_pid(victim.pid)
            tag = "✓" if ok else "✗"
            suffix = f" — {msg}" if msg else ""
            print(f"  {tag} pid {victim.pid:<6} {victim.name}{suffix}")
            if ok:
                stopped += 1
            else:
                failed += 1
    print(f"Done. Stopped {stopped}, failed {failed}.")
    return 0 if failed == 0 else 2


def _scan_windows_processes() -> list[dict[str, Any]]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        return []
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$rows = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^(node|node\.exe|python|python\.exe|pythonw|pythonw\.exe|py|py\.exe|npx|npx\.cmd|cmd|cmd\.exe|pwsh|pwsh\.exe|powershell|powershell\.exe)$' -or
    $_.CommandLine -match '(?i)(mcp|modelcontextprotocol|node|python)'
  } |
  ForEach-Object {
    $gp = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    $cpu = 0
    if ($gp -and $null -ne $gp.CPU) { $cpu = [double]$gp.CPU }
    [pscustomobject]@{
      pid = [int]$_.ProcessId
      name = [string]$_.Name
      command = [string]$_.CommandLine
      started_at = if ($_.CreationDate) { $_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }
      cpu_seconds = $cpu
    }
  }
$rows | ConvertTo-Json -Depth 3
"""
    try:
        proc = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-Command", script],
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = _loads_json_rows(proc.stdout)
    return rows if rows else _scan_windows_processes_without_command_line()


def _scan_windows_processes_without_command_line() -> list[dict[str, Any]]:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        return []
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-Process |
  Where-Object { $_.ProcessName -match '^(node|python|pythonw|py)$' } |
  Select-Object `
    @{Name='pid';Expression={[int]$_.Id}},
    @{Name='name';Expression={[string]$_.ProcessName}},
    @{Name='command';Expression={[string]$_.ProcessName}},
    @{Name='started_at';Expression={if ($_.StartTime) {$_.StartTime.ToString('yyyy-MM-dd HH:mm:ss')} else {'-'}}},
    @{Name='cpu_seconds';Expression={if ($null -ne $_.CPU) {[double]$_.CPU} else {0}}} |
  ConvertTo-Json -Depth 3
"""
    try:
        proc = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-Command", script],
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return _loads_json_rows(proc.stdout)


def _scan_posix_processes() -> list[dict[str, Any]]:
    ps = shutil.which("ps")
    if not ps:
        return []
    try:
        proc = subprocess.run(
            [ps, "-axo", "pid=,comm=,etime=,time=,args="],
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) < 5:
            continue
        pid, name, elapsed, cpu, command = parts
        try:
            pid_int = int(pid)
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid_int,
                "name": name,
                "command": command,
                "started_at": f"elapsed {elapsed}",
                "cpu_seconds": _cpu_to_seconds(cpu),
            }
        )
    return rows


def _row_to_process(row: dict[str, Any]) -> RuntimeProcess | None:
    try:
        pid = int(row.get("pid", 0))
    except (TypeError, ValueError):
        return None
    name = str(row.get("name") or "")
    command = str(row.get("command") or name)
    if not name and not command:
        return None
    if _is_scanner_self_noise(pid, command):
        return None
    kind = _classify(name, command)
    return RuntimeProcess(
        pid=pid,
        name=name,
        command=command,
        started_at=_format_started_at(row.get("started_at")),
        cpu_seconds=_float_or_zero(row.get("cpu_seconds")),
        kind=kind,
        signature=_signature(kind, command),
    )


def _is_relevant(proc: RuntimeProcess) -> bool:
    return proc.kind in {"mcp", "node", "python"}


def _is_scanner_self_noise(pid: int, command: str) -> bool:
    if pid == os.getpid():
        return True
    text = command.lower()
    return (
        "-m vbi " in text
        or "-m vbi." in text
        or "-m vbi cleanup" in text
        or "get-ciminstance win32_process" in text
        or "vbi runtime process scan" in text
    )


def _classify(name: str, command: str) -> str:
    text = f"{name} {command}".lower()
    if "mcp" in text or "modelcontextprotocol" in text:
        return "mcp"
    base = name.lower().removesuffix(".exe").removesuffix(".cmd")
    if base in {"node", "npx"}:
        return "node"
    if base in {"python", "pythonw", "py"}:
        return "python"
    if re.search(r"(^|[\\/ ])node(\.exe)?([\" ]|$)", text):
        return "node"
    if re.search(r"(^|[\\/ ])pythonw?(\.exe)?([\" ]|$)", text):
        return "python"
    return "other"


def _signature(kind: str, command: str) -> str:
    text = command.lower()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"--port[= ]\d+", "--port #", text)
    text = re.sub(r"--inspect(?:-brk)?[= ]\S+", "--inspect #", text)
    text = re.sub(r"\\+", "/", text)
    if kind == "mcp":
        match = re.search(r"(@[\w.-]+/[\w.-]*mcp[\w.-]*|[\w.-]*mcp[\w.-]*)", text)
        if match:
            return f"mcp:{match.group(1)}"
    return f"{kind}:{text}"


def _duplicate_signatures(processes: list[RuntimeProcess]) -> set[str]:
    counts: dict[str, int] = {}
    for proc in processes:
        counts[proc.signature] = counts.get(proc.signature, 0) + 1
    return {sig for sig, count in counts.items() if count > 1}


def _format_table(rows: list[RuntimeProcess], duplicates: set[str]) -> str:
    headers = ["dup", "kind", "pid", "started", "cpu(s)", "name", "command"]
    table = [headers]
    for proc in rows:
        table.append(
            [
                "yes" if proc.signature in duplicates else "-",
                proc.kind,
                str(proc.pid),
                proc.started_at,
                f"{proc.cpu_seconds:.1f}",
                proc.name,
                _truncate(proc.command, 92),
            ]
        )
    widths = [
        min(max(len(row[idx]) for row in table), 92)
        for idx in range(len(headers))
    ]
    lines = [
        "  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(table[0])),
        "  ".join("-" * width for width in widths),
    ]
    for row in table[1:]:
        lines.append("  ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))))
    return "\n".join(lines)


def _loads_json_rows(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def _format_started_at(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    if not text:
        return "-"
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text[:19]


def _cpu_to_seconds(text: str) -> float:
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return 0.0
    return 0.0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _truncate(text: str, limit: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _started_sort_key(proc: RuntimeProcess) -> tuple:
    # Newest first: parsed datetime descending, then highest PID descending.
    # Falls back to PID-only ordering when started_at isn't ISO-formatted
    # (POSIX `ps etime=` produces elapsed strings, not timestamps).
    try:
        dt = datetime.strptime(proc.started_at, "%Y-%m-%d %H:%M:%S")
        return (0, -dt.timestamp(), -proc.pid)
    except ValueError:
        return (1, -proc.pid)


def build_cleanup_plan(processes: list[RuntimeProcess]) -> list[dict[str, Any]]:
    by_sig: dict[str, list[RuntimeProcess]] = {}
    for proc in processes:
        by_sig.setdefault(proc.signature, []).append(proc)
    plan: list[dict[str, Any]] = []
    for sig, group in by_sig.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=_started_sort_key)
        plan.append({"signature": sig, "keep": ordered[0], "kill": ordered[1:]})
    plan.sort(key=lambda entry: entry["signature"])
    return plan


def _render_cleanup_plan(plan: list[dict[str, Any]]) -> str:
    total_kill = sum(len(entry["kill"]) for entry in plan)
    lines = [
        f"Plan: stop {total_kill} older duplicate(s), keep {len(plan)} newest "
        f"(one per group)",
        "",
    ]
    headers = ["group", "signature", "keep_pid", "kill_pids"]
    rows: list[list[str]] = [headers]
    for idx, entry in enumerate(plan, 1):
        rows.append([
            str(idx),
            _truncate(entry["signature"], 60),
            str(entry["keep"].pid),
            ", ".join(str(v.pid) for v in entry["kill"]),
        ])
    widths = [
        min(max(len(row[i]) for row in rows), 60) for i in range(len(headers))
    ]
    out = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(rows[0])),
        "  ".join("-" * width for width in widths),
    ]
    for row in rows[1:]:
        out.append("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    lines.append("\n".join(out))
    return "\n".join(lines)


def filter_plan_by_groups(
    plan: list[dict[str, Any]],
    groups: str | None,
) -> list[dict[str, Any]]:
    if not groups:
        return plan
    patterns = [p.strip() for p in groups.split(",") if p.strip()]
    if not patterns:
        return plan
    return [
        entry for entry in plan
        if any(fnmatch.fnmatchcase(entry["signature"], pat) for pat in patterns)
    ]


def _render_signature_list(plan: list[dict[str, Any]]) -> str:
    lines = [f"Duplicate group signatures ({len(plan)}):"]
    for entry in plan:
        sig = _truncate(entry["signature"], 76)
        lines.append(f"  {sig}  ({len(entry['kill']) + 1} procs)")
    return "\n".join(lines)


def terminate_pid(pid: int) -> tuple[bool, str]:
    if pid <= 0 or pid == os.getpid():
        return False, "skipped self/invalid pid"
    try:
        if os.name == "nt":
            res = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            if res.returncode == 0:
                return True, ""
            err = (res.stderr or res.stdout or "").strip().splitlines()
            return False, (err[-1] if err else f"taskkill exit {res.returncode}")
        import signal
        os.kill(pid, signal.SIGTERM)
        return True, ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
