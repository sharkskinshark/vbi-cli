"""Adapter cache read/write helpers.

Cache lives at ``~/.vbi/cache/<safe_record_id>.json`` (user-scoped, never in
the repo). Each adapter writes its NormalizedRecord here on ``sync()`` and
reads from here on ``read_cache()``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import NormalizedRecord


def cache_root() -> Path:
    return Path.home() / ".vbi" / "cache"


def _safe_name(record_id: str) -> str:
    return record_id.replace("/", "_").replace(":", "_").replace("\\", "_")


def cache_path(record_id: str) -> Path:
    return cache_root() / f"{_safe_name(record_id)}.json"


def write_cache_record(record: NormalizedRecord) -> None:
    root = cache_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    path = cache_path(record.record_id)
    try:
        path.write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def read_cache_record(record_id: str) -> NormalizedRecord | None:
    path = cache_path(record_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return NormalizedRecord(**data)
    except TypeError:
        return None
