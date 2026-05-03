"""Release safety audit for vbi-cli."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path
import re
import subprocess


SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    "build",
}

SKIP_DIR_PATTERNS = (
    "*.egg-info",
    "pytest-cache-files-*",
)

SENSITIVE_DIR_NAMES = {
    ".claude",
    ".gcloud",
    ".google-mcp",
}

RUNTIME_ARTIFACT_NAMES = {
    ".credentials.json",
    "accounts.json",
    "auth.json",
    "live_usage.json",
    "mcp-oauth-tokens.json",
    "oauth_creds.json",
    "config.yaml",
    "vbi.entitlements.yaml",
}

RUNTIME_ARTIFACT_SUFFIXES = {
    ".sqlite",
    ".sqlite3",
    ".db",
    ".jsonl",
    ".pyc",
}

REQUIRED_GITIGNORE_PATTERNS = {
    ".env",
    "*.env",
    "oauth_creds.json",
    "config.yaml",
    "vbi.entitlements.yaml",
    "public/live_usage.json",
    "runtime-output/",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.jsonl",
    ".claude/",
    ".gcloud/",
    ".google-mcp/",
    "__pycache__/",
    ".venv/",
    "*.egg-info/",
    "pytest-cache-files-*/",
}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{40,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b"),
    re.compile(r"\bvercel_[A-Za-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(r'"(?:access_token|refresh_token|client_secret)"\s*:\s*"[^"]{12,}"'),
    re.compile(r"(?i)\b(access_token|refresh_token|client_secret|api_key)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"),
    re.compile(r"(?i)\b(password|passwd)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
]

PII_PATTERNS = [
    re.compile(r"(?i)\b[A-Z0-9._%+-]+@(?:gmail|hotmail|outlook|yahoo)\.com\b"),
    re.compile(r"(?i)C:\\Users\\(?!USER|USERNAME|example)[^\\\s]+"),
    re.compile(r"(?i)/" + r"Users/" + r"(?!user|example)[^/\s]+"),
]

SAFE_TEXT_EXTENSIONS = {
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".gitignore",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    path: str
    line: int
    rule: str
    message: str


def _is_skipped(path: Path, root: Path) -> bool:
    rel_parts = path.relative_to(root).parts
    return any(_is_skipped_dir_name(part) for part in rel_parts)


def _is_skipped_dir_name(name: str) -> bool:
    return name in SKIP_DIRS or any(
        fnmatch.fnmatch(name, pattern) for pattern in SKIP_DIR_PATTERNS
    )


def _is_text_candidate(path: Path) -> bool:
    return path.suffix in SAFE_TEXT_EXTENSIONS or path.name in SAFE_TEXT_EXTENSIONS


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace(os.sep, "/")


def _scan_artifact(path: Path, root: Path) -> list[Finding]:
    rel = _relative(path, root)
    findings: list[Finding] = []
    rel_parts = path.relative_to(root).parts
    if any(part in SENSITIVE_DIR_NAMES for part in rel_parts):
        findings.append(Finding("critical", rel, 0, "sensitive-dir-artifact", "local tool credential/state directory must not be in the release tree"))
    if path.name in RUNTIME_ARTIFACT_NAMES or path.suffix in RUNTIME_ARTIFACT_SUFFIXES:
        findings.append(Finding("critical", rel, 0, "runtime-artifact", "runtime or credential artifact must not be committed"))
    if "runtime-output" in path.relative_to(root).parts:
        findings.append(Finding("critical", rel, 0, "runtime-output", "generated runtime output must not be committed"))
    return findings


def _scan_text(path: Path, root: Path) -> list[Finding]:
    rel = _relative(path, root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return [Finding("warning", rel, 0, "encoding", "file is not valid UTF-8 text")]
    except OSError as exc:
        return [Finding("warning", rel, 0, "read-error", f"file could not be read: {exc}")]

    findings: list[Finding] = []
    for index, line in enumerate(lines, start=1):
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding("critical", rel, index, "secret", "possible secret-like assignment"))
        for pattern in PII_PATTERNS:
            if pattern.search(line):
                findings.append(Finding("critical", rel, index, "pii", "possible personal identifier or local absolute path"))
        if (chr(96) + "r" + chr(96) + "n") in line:
            findings.append(Finding("warning", rel, index, "format", "literal PowerShell newline marker found"))
    return findings


def _scan_directory(path: Path, root: Path) -> list[Finding]:
    rel = _relative(path, root)
    if path.name in SENSITIVE_DIR_NAMES:
        return [Finding("critical", rel, 0, "sensitive-dir-artifact", "local tool credential/state directory must not be in the release tree")]
    if path.name == "runtime-output":
        return [Finding("critical", rel, 0, "runtime-output", "generated runtime output must not be in the release tree")]
    return []


def _git_output(root: Path, args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _scan_gitignore(root: Path) -> list[Finding]:
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return [Finding("critical", ".gitignore", 0, "missing-gitignore", "repository has no .gitignore")]
    try:
        lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as exc:
        return [Finding("critical", ".gitignore", 0, "read-error", f".gitignore could not be read: {exc}")]

    present = {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}
    findings = []
    for pattern in sorted(REQUIRED_GITIGNORE_PATTERNS):
        if pattern not in present:
            findings.append(Finding("critical", ".gitignore", 0, "incomplete-gitignore", f"missing required ignore pattern: {pattern}"))
    return findings


def _tracked_files(root: Path) -> tuple[list[str], list[Finding]]:
    code, stdout, stderr = _git_output(root, ["ls-files", "-z"])
    if code != 0:
        msg = stderr.strip() or "git ls-files failed"
        return [], [Finding("warning", ".", 0, "git-unavailable", msg)]
    return [item for item in stdout.split("\0") if item], []


def _scan_tracked_files(root: Path) -> list[Finding]:
    tracked, findings = _tracked_files(root)
    for rel in tracked:
        path = root / rel
        parts = Path(rel).parts
        if any(part in SENSITIVE_DIR_NAMES for part in parts):
            findings.append(Finding("critical", rel, 0, "tracked-sensitive-dir", "tracked local tool credential/state directory"))
        if path.name in RUNTIME_ARTIFACT_NAMES or path.suffix in RUNTIME_ARTIFACT_SUFFIXES:
            findings.append(Finding("critical", rel, 0, "tracked-runtime-artifact", "tracked runtime or credential artifact"))
        if "runtime-output" in parts:
            findings.append(Finding("critical", rel, 0, "tracked-runtime-output", "tracked generated runtime output"))
        if _is_text_candidate(path):
            findings.extend(_scan_text(path, root))
    return findings


def _scan_git_history(root: Path, max_findings: int = 12) -> list[Finding]:
    code, stdout, stderr = _git_output(root, ["log", "--all", "-p", "--no-ext-diff"])
    if code != 0:
        msg = stderr.strip() or "git history scan failed"
        return [Finding("warning", ".", 0, "history-scan-unavailable", msg)]

    findings: list[Finding] = []
    current_file = "(git history)"
    for line in stdout.splitlines():
        if line.startswith("+++ b/") or line.startswith("--- a/"):
            current_file = line[6:]
            continue
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(Finding("critical", current_file, 0, "history-secret", "possible secret appears in git history"))
                break
        if len(findings) >= max_findings:
            findings.append(Finding("warning", "(git history)", 0, "history-truncated", f"history scan stopped after {max_findings} findings"))
            break
    return findings


def _dedupe(findings: list[Finding]) -> list[Finding]:
    seen: set[Finding] = set()
    unique: list[Finding] = []
    for item in findings:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def run_audit(root: Path, include_history: bool = True) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    findings.extend(_scan_gitignore(root))
    findings.extend(_scan_tracked_files(root))

    def _on_walk_error(exc: OSError) -> None:
        path = getattr(exc, "filename", ".") or "."
        try:
            rel = _relative(Path(path), root)
        except ValueError:
            rel = str(path)
        findings.append(Finding("warning", rel, 0, "walk-error", f"path could not be scanned: {exc}"))

    for current, dirs, files in os.walk(root, onerror=_on_walk_error):
        visible_dirs = [name for name in dirs if not _is_skipped_dir_name(name)]
        current_path = Path(current)
        for name in visible_dirs:
            findings.extend(_scan_directory(current_path / name, root))
        dirs[:] = [
            name for name in visible_dirs
            if name not in SENSITIVE_DIR_NAMES and name != "runtime-output"
        ]
        for name in files:
            path = current_path / name
            if _is_skipped(path, root):
                continue
            findings.extend(_scan_artifact(path, root))
            if _is_text_candidate(path):
                findings.extend(_scan_text(path, root))

    if include_history:
        findings.extend(_scan_git_history(root))

    return _dedupe(findings)


def render_findings(findings: list[Finding]) -> str:
    if not findings:
        return "PASS: no critical release-gate findings detected"

    lines = ["Release gate findings:"]
    for item in findings:
        location = item.path if item.line == 0 else f"{item.path}:{item.line}"
        lines.append(f"[{item.severity}] {location} {item.rule}: {item.message}")
    return "\n".join(lines)


def has_critical(findings: list[Finding]) -> bool:
    return any(item.severity == "critical" for item in findings)

