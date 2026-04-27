"""Release safety audit for vbi-cli."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    "build",
    "*.egg-info",
}

RUNTIME_ARTIFACT_NAMES = {
    "live_usage.json",
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

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
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
    return any(part in SKIP_DIRS for part in rel_parts)


def _is_text_candidate(path: Path) -> bool:
    return path.suffix in SAFE_TEXT_EXTENSIONS or path.name in SAFE_TEXT_EXTENSIONS


def _scan_artifact(path: Path, root: Path) -> list[Finding]:
    rel = str(path.relative_to(root))
    findings: list[Finding] = []
    if path.name in RUNTIME_ARTIFACT_NAMES or path.suffix in RUNTIME_ARTIFACT_SUFFIXES:
        findings.append(Finding("critical", rel, 0, "runtime-artifact", "runtime or credential artifact must not be committed"))
    if "runtime-output" in path.relative_to(root).parts:
        findings.append(Finding("critical", rel, 0, "runtime-output", "generated runtime output must not be committed"))
    return findings


def _scan_text(path: Path, root: Path) -> list[Finding]:
    rel = str(path.relative_to(root))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return [Finding("warning", rel, 0, "encoding", "file is not valid UTF-8 text")]

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


def run_audit(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in root.rglob("*"):
        if path.is_dir() or _is_skipped(path, root):
            continue
        findings.extend(_scan_artifact(path, root))
        if _is_text_candidate(path):
            findings.extend(_scan_text(path, root))
    return findings


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

