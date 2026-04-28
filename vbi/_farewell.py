"""Ctrl+C handler that lands sub-modes on an interactive home prompt.

Behaviour:
  • Inside `vbi live` / `vbi dashboard`, one Ctrl+C ends the sub-mode and
    drops you onto the home view — same mini-banner + quick-start menu
    shown after install — followed by an interactive `vbi> ` prompt.
  • At the prompt: type a vbi sub-command (e.g. `live`, `dashboard`, `map`,
    `sync`, `--help`) and it runs as `python -m vbi <args>`. When that
    sub-command returns, you land back on the same prompt.
  • Press Ctrl+C — or type `exit` / `quit` / `q` — to fully exit vbi back
    to your shell. (We used to require Ctrl+C twice, but on Windows the
    timing of the warning print racing against the next ``input()`` call
    made the behaviour unreliable; a single tap is simpler and works
    consistently across hosts.)
"""
from __future__ import annotations

import difflib
import os
import subprocess
import sys

from .splash import _GRADIENT_L, _GRADIENT_R, _gradient_line, _version


_AMBER  = "\033[38;5;215m"
_ORANGE = "\033[38;5;208m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"
_RST    = "\033[0m"
_SEP    = "─" * 65

_EXIT_WORDS = {"exit", "quit", "q"}

# Known top-level vbi sub-commands (mirrors cli.COMMANDS plus --help).
# Used by the home prompt to catch typos and suggest fixes BEFORE shelling
# out, so the user gets `unknown command: 'dasboard' — did you mean
# 'dashboard'?` instead of argparse's full usage dump.
_KNOWN_COMMANDS = (
    "doctor", "init", "sync", "status", "inventory",
    "dashboard", "live", "audit", "export", "update", "map",
    "--help", "-h",
)


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
        f"  {_BOLD}VBI CLI{_RST}  {_DIM}v{_version()}{_RST}\n"
        f"  {tagline}\n"
        f"  {_DIM}Read-only · no credentials · no network{_RST}\n"
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
    own Ctrl+C handling and we resume cleanly when it ends. Unknown commands
    are caught here with a suggestion instead of leaking argparse's usage
    dump back to the prompt."""
    parts = cmd_text.split()
    if not parts:
        return
    head = parts[0].lower()

    if head not in _KNOWN_COMMANDS:
        suggestion = difflib.get_close_matches(head, _KNOWN_COMMANDS, n=1, cutoff=0.6)
        if suggestion:
            print(
                f"  \033[91munknown command:\033[0m '{head}' — did you mean "
                f"'{_ORANGE}{suggestion[0]}{_RST}'?"
            )
        else:
            print(
                f"  \033[91munknown command:\033[0m '{head}'. "
                f"Type {_ORANGE}--help{_RST} for the full list."
            )
        return

    try:
        subprocess.run([sys.executable, "-m", "vbi"] + parts)
    except KeyboardInterrupt:
        pass


class CtrlCExit:
    """Sub-mode exit handler used by `vbi live` / `vbi dashboard`."""

    def footer(self, idle_text: str) -> str:
        return idle_text

    def handle_interrupt(self) -> bool:
        """Print home view + run an interactive prompt until the user exits.

        A single Ctrl+C (or `exit` / `quit` / `q`, or Ctrl+D) terminates
        the loop and signals the calling sub-mode to ``return 0``.
        """
        print(_home_view())
        print(
            f"  {_DIM}type a command (live, dashboard, map, sync, --help)"
            f" or Ctrl+C to exit vbi{_RST}"
        )
        prompt = f"  {_ORANGE}vbi>{_RST} "
        while True:
            try:
                cmd = input(prompt).strip()
            except (KeyboardInterrupt, EOFError):
                print()  # newline so the shell prompt sits on a clean row
                return True

            if not cmd:
                continue
            if cmd.lower() in _EXIT_WORDS:
                return True

            _run_subcommand(cmd)
            # After the spawned command finishes, fall through and re-prompt.
