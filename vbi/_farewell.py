"""Ctrl+C handler that lands sub-modes on an interactive home prompt.

Behaviour:
  • Inside `vbi live` / `vbi dashboard`, one Ctrl+C ends the sub-mode and
    drops you onto the home view — same mini-banner + quick-start menu
    shown after install — followed by an interactive `vbi> ` prompt.
  • At the prompt: type a vbi sub-command (e.g. `live`, `dashboard`, `map`,
    `sync`, `--help`) and it runs as `python -m vbi <args>`. When that
    sub-command returns, you land back on the same prompt.
  • Press Ctrl+C twice within ~2 s — or type `exit` / `quit` / `q` — to
    fully exit vbi back to your shell.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from .splash import _GRADIENT_L, _GRADIENT_R, _gradient_line, _version


_AMBER  = "\033[38;5;215m"
_ORANGE = "\033[38;5;208m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"
_YELLOW = "\033[93m"
_RST    = "\033[0m"
_SEP    = "─" * 65

_DOUBLE_TAP_WINDOW = 2.0
_EXIT_WORDS = {"exit", "quit", "q"}


def _home_view() -> str:
    """Mini banner + quick-start menu — same look as the install summary."""
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if not use_color:
        return (
            "\n"
            f"  VBI CLI  v{_version()}\n"
            "  Local-first AI usage inspection\n"
            "  Read-only · no credentials · no network\n"
            "\n"
            f"  {_SEP}\n"
            "    live       real-time bar chart, syncs each refresh\n"
            "    dashboard  cached view (no sync, no network)\n"
            "    map        AI tooling host-first map\n"
            "    sync       refresh provider caches\n"
            "    --help     all commands\n"
            f"  {_SEP}\n"
        )
    tagline = _gradient_line(
        "Local-first AI usage inspection",
        len("Local-first AI usage inspection"),
        _GRADIENT_L, _GRADIENT_R,
    )
    return (
        "\n"
        f"  {_AMBER}▟═{_RST}    {_BOLD}VBI CLI{_RST}  {_DIM}v{_version()}{_RST}\n"
        f"  {_AMBER}╤╥{_RST}    {tagline}\n"
        f"  {_AMBER}▒▒{_RST}    {_DIM}Read-only · no credentials · no network{_RST}\n"
        f"  {_AMBER}▲█{_RST}\n"
        "\n"
        f"  {_DIM}{_SEP}{_RST}\n"
        f"    {_ORANGE}live{_RST}       {_DIM}real-time bar chart, syncs each refresh{_RST}\n"
        f"    {_ORANGE}dashboard{_RST}  {_DIM}cached view (no sync, no network){_RST}\n"
        f"    {_ORANGE}map{_RST}        {_DIM}AI tooling host-first map{_RST}\n"
        f"    {_ORANGE}sync{_RST}       {_DIM}refresh provider caches{_RST}\n"
        f"    {_ORANGE}--help{_RST}     {_DIM}all commands{_RST}\n"
        f"  {_DIM}{_SEP}{_RST}\n"
    )


def _run_subcommand(cmd_text: str) -> None:
    """Spawn `python -m vbi <args>` so the typed command runs with the user's
    own Ctrl+C handling and we resume cleanly when it ends."""
    try:
        subprocess.run([sys.executable, "-m", "vbi"] + cmd_text.split())
    except KeyboardInterrupt:
        # User Ctrl+C'd the spawned command; just resume the prompt.
        pass


class CtrlCExit:
    """Sub-mode exit handler used by `vbi live` / `vbi dashboard`."""

    def footer(self, idle_text: str) -> str:
        return idle_text

    def handle_interrupt(self) -> bool:
        """Print home view + run an interactive prompt until the user fully
        exits (Ctrl+C twice within window, or types `exit`). Returns True so
        the calling sub-mode loop returns 0 once we're done."""
        print(_home_view())
        print(
            f"  {_DIM}type a command (live, dashboard, map, sync, --help)"
            f" or Ctrl+C twice to exit vbi{_RST}"
        )
        prompt = f"  {_ORANGE}vbi>{_RST} "
        last_tap = 0.0
        while True:
            try:
                cmd = input(prompt).strip()
            except KeyboardInterrupt:
                now = time.time()
                if now - last_tap < _DOUBLE_TAP_WINDOW:
                    print()  # blank line after ^C so shell prompt is clean
                    return True
                last_tap = now
                print(f"\n  {_YELLOW}⚠ press Ctrl+C again to exit{_RST}")
                continue
            except EOFError:
                # Ctrl+D / closed stdin → exit gracefully.
                return True

            if not cmd:
                continue
            if cmd.lower() in _EXIT_WORDS:
                return True

            _run_subcommand(cmd)
            # After the spawned command finishes, fall through and re-prompt.
