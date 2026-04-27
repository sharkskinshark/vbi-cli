"""Provider adapter registry.

Provider adapters declare a ``record_id`` (matching an inventory descriptor
when the tool is also discoverable via ``vbi inventory``) and an
``adapter_tier`` of either ``"scaffold"`` or ``"live"``. Real adapters that
fetch usage default to ``"live"``; the unavailable scaffold reports
``"scaffold"``.
"""

from __future__ import annotations

from typing import Literal

from vbi.providers.antigravity import AntigravityAdapter
from vbi.providers.claude_code import ClaudeCodeAdapter
from vbi.providers.codex_cli import CodexCliAdapter
from vbi.providers.gemini_cli import GeminiCliAdapter
from vbi.providers.unavailable import UnavailableProviderAdapter


AdapterTier = Literal["none", "scaffold", "live"]


def get_adapters() -> list[object]:
    return [
        AntigravityAdapter(),
        ClaudeCodeAdapter(),
        CodexCliAdapter(),
        GeminiCliAdapter(),
        UnavailableProviderAdapter(),
    ]


def find_adapter(record_id: str) -> object | None:
    for adapter in get_adapters():
        if getattr(adapter, "record_id", None) == record_id:
            return adapter
    return None


def adapter_status_for_record(record_id: str) -> AdapterTier:
    adapter = find_adapter(record_id)
    if adapter is None:
        return "none"
    tier = getattr(adapter, "adapter_tier", "live")
    if tier in ("scaffold", "live"):
        return tier  # type: ignore[return-value]
    return "live"
