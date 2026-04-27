"""`vbi update` — check for newer source and refresh the editable install.

Strategy: vbi-cli is installed editable from a git clone. Updating means:
  1. ``git fetch`` then check how many commits we are behind upstream
  2. If behind, ``git pull --ff-only`` and re-run ``pip install -e .``

A cached startup hint (in splash.py) checks at most once per 24h so the
update banner doesn't add network latency to every ``vbi live`` startup.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import vbi


_CHECK_CACHE = Path.home() / ".vbi" / "last_update_check.json"
_CHECK_INTERVAL_SECS = 24 * 3600  # at most once per day


def source_dir() -> Path | None:
    """Return the editable-install source directory, or None if not editable."""
    init_path = Path(vbi.__file__).resolve()
    src = init_path.parent.parent
    if (src / "pyproject.toml").is_file() and (src / ".git").is_dir():
        return src
    return None


def _git(*args: str, cwd: Path, timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, "", "git unavailable or timed out"


def check_updates(src: Path, fetch: bool = True) -> tuple[int, str | None]:
    """Return (commits_behind, latest_subject_or_None).

    With fetch=False, only inspects the local git state — no network call.
    """
    if fetch:
        _git("fetch", "--quiet", cwd=src, timeout=15)
    rc, count_str, _ = _git("rev-list", "--count", "HEAD..@{upstream}", cwd=src, timeout=5)
    if rc != 0:
        return 0, None
    try:
        count = int(count_str or "0")
    except ValueError:
        return 0, None
    if count <= 0:
        return 0, None
    rc, subject, _ = _git("log", "@{upstream}", "-1", "--format=%s", cwd=src, timeout=5)
    return count, subject if rc == 0 else None


def maybe_check_cached() -> tuple[int, str | None]:
    """Cached check (network at most once per 24h). Returns (count, subject).

    Used by the startup splash to show a non-blocking update hint without
    adding multi-second latency to every ``vbi live`` boot.
    """
    src = source_dir()
    if src is None:
        return 0, None

    now = time.time()
    last = 0.0
    cached_count = 0
    cached_subject: str | None = None
    if _CHECK_CACHE.is_file():
        try:
            import json
            data = json.loads(_CHECK_CACHE.read_text(encoding="utf-8"))
            last = float(data.get("ts", 0))
            cached_count = int(data.get("count", 0))
            cached_subject = data.get("subject")
        except Exception:  # noqa: BLE001
            pass

    if now - last < _CHECK_INTERVAL_SECS:
        return cached_count, cached_subject

    count, subject = check_updates(src, fetch=True)
    try:
        _CHECK_CACHE.parent.mkdir(parents=True, exist_ok=True)
        import json
        _CHECK_CACHE.write_text(
            json.dumps({"ts": now, "count": count, "subject": subject}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return count, subject


def run_update(check_only: bool = False) -> int:
    src = source_dir()
    if src is None:
        print("vbi update: this is not an editable install; nothing to update.")
        print("            (re-run install.ps1 to set up an updateable copy.)")
        return 1

    print(f"Source:  {src}")
    print("Checking for updates...")
    count, subject = check_updates(src, fetch=True)
    if count == 0:
        print("Already up to date.")
        return 0

    print(f"Behind by {count} commits.  Latest: {subject}")
    if check_only:
        print("Run `vbi update` (without --check) to pull and reinstall.")
        return 0

    print("Pulling...")
    rc, out, err = _git("pull", "--ff-only", cwd=src, timeout=60)
    if rc != 0:
        print(f"git pull failed:\n{err or out}")
        return rc

    print("Refreshing dependencies (pip install -e .)...")
    pip_rc = subprocess.call([
        sys.executable, "-m", "pip", "install",
        "--quiet", "--disable-pip-version-check", "--upgrade", "-e", str(src),
    ])
    if pip_rc != 0:
        print("pip refresh failed; the source pulled but deps may be stale.")
        return pip_rc

    # Invalidate cache so next `vbi live` doesn't show stale hint
    try:
        _CHECK_CACHE.unlink(missing_ok=True)
    except OSError:
        pass

    print("Updated.")
    return 0
