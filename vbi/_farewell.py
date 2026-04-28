"""Ctrl+C handler that lands sub-modes on an interactive home prompt.

Behaviour:
  • Inside `vbi live` / `vbi dashboard`, one Ctrl+C ends the sub-mode and
    drops you onto the home view — same mini-banner + quick-start menu
    shown after install — followed by an interactive `vbi> ` prompt.
  • Every sub-command typed at the prompt (`live`, `dashboard`, `map`,
    `sync`, `--help`, …) runs as `python -m vbi <args>`. When it
    finishes the output stays on screen so you can read it; a fresh
    `vbi> ` prompt appears immediately below. Press Ctrl+C when you're
    done reading to clear the screen and re-show the home view.
  • Pressing Ctrl+C at the prompt re-grounds the user: 1st tap clears
    the screen and reprints the home view; a 2nd tap within ~2 s exits
    vbi back to the shell. Typing `exit` / `quit` / `q` (or Ctrl+D)
    exits immediately.
"""
from __future__ import annotations

import difflib
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

# Farewell shown when the user presses Ctrl+C twice in the home prompt to
# fully exit vbi. Mirrors the start-up banner's warm orange→gold gradient
# so the start and the end of a session bookend visually.
_FAREWELL_SKYLINE  = "▂▅▃▆▂▇▄█▃▆▂▅▃▆"
_FAREWELL_TAGLINE  = "Inspector signing off — local data stays local."
_FAREWELL_FULLNAME = "Visual Budget Inspection"

_EXIT_WORDS = {"exit", "quit", "q"}

# Window (seconds) used to absorb queued Ctrl+C signals AFTER a subcommand
# exits. Reason: on Windows, CTRL_C_EVENT is broadcast to every process in
# the console group, so when the user presses Ctrl+C inside `live` /
# `dashboard`, the parent (this REPL) typically receives MORE THAN ONE KbI.
# Without this drain, the second queued signal fires during the next
# input() call at the home prompt, instantly burning through the warning
# state and exiting vbi — exactly the "first Ctrl+C at home directly
# exits" symptom users hit on the 2nd run-through.
_KBI_DRAIN_WINDOW_S = 0.2

# Commands that own the full terminal while running (clear-screen loop).
# After they exit we always re-show the home view so the user isn't left
# staring at the last rendered frame.
_FULLSCREEN_CMDS = {"live", "dashboard"}


def _drain_pending_kbi(window: float = _KBI_DRAIN_WINDOW_S) -> None:
    """Absorb queued Ctrl+C signals before returning to the home prompt.

    Sleeps in short slices for ``window`` seconds. If a KeyboardInterrupt
    fires (i.e. the OS had a queued signal to deliver), we swallow it and
    reset the deadline so a burst of signals can fully drain. When the
    window passes without a KbI, we know the queue is empty.
    """
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        try:
            time.sleep(0.05)
        except KeyboardInterrupt:
            deadline = time.monotonic() + window

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
            "  Read-only · no credentials · no provider API calls\n"
            "\n"
            f"  {_SEP}\n"
            "    live       real-time bar chart, syncs each refresh\n"
            "    dashboard  cached view (no sync, no network)\n"
            "    map        AI tooling host-first map\n"
            "    sync       refresh provider caches\n"
            "    export     write sanitized JSON report to ~\n"
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
        f"  {_DIM}Read-only · no credentials · no provider API calls{_RST}\n"
        "\n"
        f"  {_DIM}{_SEP}{_RST}\n"
        f"    {_ORANGE}live{_RST}       {_DIM}real-time bar chart, syncs each refresh{_RST}\n"
        f"    {_ORANGE}dashboard{_RST}  {_DIM}cached view (no sync, no network){_RST}\n"
        f"    {_ORANGE}map{_RST}        {_DIM}AI tooling host-first map{_RST}\n"
        f"    {_ORANGE}sync{_RST}       {_DIM}refresh provider caches{_RST}\n"
        f"    {_ORANGE}export{_RST}     {_DIM}write sanitized JSON report to ~{_RST}\n"
        f"    {_ORANGE}--help{_RST}     {_DIM}all commands{_RST}\n"
        f"  {_DIM}{_SEP}{_RST}\n"
    )


def _print_farewell() -> None:
    """Goodbye easter-egg shown when the user actually leaves vbi.

    Mini skyline + tagline, both in the warm orange→gold gradient that
    matches the start-up `VBI CLI` banner. On non-TTYs (piped output,
    redirects) we fall back to plain text.
    """
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    if not use_color:
        print()
        print(f"  {_FAREWELL_SKYLINE}")
        print(f"  {_FAREWELL_TAGLINE}")
        print(f"  {_FAREWELL_FULLNAME}")
        print()
        return
    sky_line  = _gradient_line(_FAREWELL_SKYLINE,  len(_FAREWELL_SKYLINE),  _GRADIENT_L, _GRADIENT_R)
    text_line = _gradient_line(_FAREWELL_TAGLINE,  len(_FAREWELL_TAGLINE),  _GRADIENT_L, _GRADIENT_R)
    name_line = _gradient_line(_FAREWELL_FULLNAME, len(_FAREWELL_FULLNAME), _GRADIENT_L, _GRADIENT_R)
    print()
    print(f"  {sky_line}")
    print(f"  {text_line}")
    print(f"  {name_line}")
    print()


def _run_subcommand(cmd_text: str) -> bool:
    """Spawn `python -m vbi <args>` so the typed command runs with the user's
    own Ctrl+C handling and we resume cleanly when it ends. Unknown commands
    are caught here with a suggestion instead of leaking argparse's usage
    dump back to the prompt.

    Returns True if Ctrl+C was caught during subprocess execution — the
    caller uses this to arm the double-tap exit window so a follow-up
    Ctrl+C at the next prompt fires immediately.
    """
    parts = cmd_text.split()
    if not parts:
        return False
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
        return False

    # Use Popen + explicit wait so KeyboardInterrupt is reliably caught even
    # when the child exits a split-second before the parent's signal fires.
    # On Windows, CTRL_C_EVENT is broadcast to *every* process in the console
    # group, so the parent receives KbI alongside the child — sometimes more
    # than once. We drain repeats by looping the wait until the child is done.
    proc = subprocess.Popen([sys.executable, "-m", "vbi"] + parts)
    interrupted = False
    while True:
        try:
            proc.wait()
            break
        except KeyboardInterrupt:
            interrupted = True
            # Loop again: child may already have exited, or may still be
            # cleaning up. Either way, keep waiting and swallow extra signals.
            continue
    # Exit code 130 is the POSIX/Windows convention for Ctrl+C termination;
    # vbi commands that propagate KbI out of cli.main() return this.
    if proc.returncode == 130:
        interrupted = True
    return interrupted


class CtrlCExit:
    """Sub-mode exit handler used by `vbi live` / `vbi dashboard`."""

    def footer(self, idle_text: str) -> str:
        return idle_text

    def handle_interrupt(self) -> bool:
        """Print home view + run interactive prompt with two-tap exit.

        Robustness note: on Windows CTRL_C_EVENT is delivered to every
        process in the console group, so the parent (this REPL) can receive
        KbI alongside its child subprocess — sometimes redelivered. We
        therefore wrap every redraw / branch in ``_safe_redraw`` so a queued
        signal can never escape this handler and silently exit vbi.
        """
        prompt = f"  {_ORANGE}vbi>{_RST} "
        # home_is_fresh : True  = home view is on screen right now
        #                 False = command output is on screen
        # warned        : True  = warning already shown; next Ctrl+C exits
        home_is_fresh = False
        warned = False

        def _safe_redraw(armed: bool) -> None:
            """Redraw the home view, absorbing any queued KbIs that fire
            during the redraw itself. Without this, a stray second Ctrl+C
            (delivered after the user's first one for the live/dashboard
            child) could fire mid-redraw and propagate up, exiting vbi."""
            while True:
                try:
                    self._show_home_fresh(armed=armed)
                    return
                except KeyboardInterrupt:
                    continue

        # Drain any signals that may have been queued during splash_sync()
        # before we land on the first input() — otherwise the first
        # Ctrl+C the user presses can race with a leftover signal.
        _drain_pending_kbi()
        _safe_redraw(armed=False)
        home_is_fresh = True

        while True:
            try:
                cmd = input(prompt).strip()
            except KeyboardInterrupt:
                if home_is_fresh:
                    if warned:
                        # 2nd Ctrl+C at home → farewell + exit.
                        _print_farewell()
                        return True
                    # 1st Ctrl+C at home → show warning.
                    warned = True
                    _safe_redraw(armed=True)
                else:
                    # Ctrl+C while command output is on screen → clean home.
                    home_is_fresh = True
                    warned = False
                    _safe_redraw(armed=False)
                continue
            except EOFError:
                _print_farewell()
                return True

            if not cmd:
                continue
            if cmd.lower() in _EXIT_WORDS:
                _print_farewell()
                return True

            # A real command resets the warning state.
            home_is_fresh = False
            warned = False
            fullscreen = cmd.split()[0].lower() in _FULLSCREEN_CMDS
            try:
                interrupted = _run_subcommand(cmd)
            except KeyboardInterrupt:
                interrupted = True

            if interrupted or fullscreen:
                home_is_fresh = True
                warned = False
                _safe_redraw(armed=False)
                # CRITICAL: drain queued signals from the subcommand's
                # Ctrl+C broadcast. Without this, the next input() at
                # the home prompt fires the queued KbI immediately,
                # advances `warned` to True on the first user keypress,
                # and exits on the next — looking exactly like "home
                # Ctrl+C directly exits with no warning".
                _drain_pending_kbi()
            # else: output stays on screen; home_is_fresh remains False.

    @staticmethod
    def _show_home_fresh(armed: bool = False) -> None:
        """Clear the visible terminal area and reprint the home view.

        Belt-and-braces: emit the ANSI clear-screen + cursor-home escape
        AND fall back to ``cls``/``clear``. Some hosts (notably xterm.js
        in nested contexts) ignore one but honour the other.

        If ``armed`` is True (1st Ctrl+C just landed and we're inside the
        2-second double-tap window) the footer flips to a yellow warning
        prompting the user to confirm exit.
        """
        # ANSI clear-screen + cursor-home. Do NOT use os.system("cls") here:
        # on Windows it spawns cmd.exe which can re-deliver CTRL_C_EVENT to
        # the parent Python process, causing a second KeyboardInterrupt that
        # escapes the handler and exits vbi silently.
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
        print(_home_view())
        if armed:
            print(
                f"  {_YELLOW}⚠ press Ctrl+C again to exit vbi{_RST}"
            )
        else:
            print(
                f"  {_DIM}type a command (live, dashboard, map, sync, export, --help)"
                f"  ·  Ctrl+C twice to exit, or type 'exit'{_RST}"
            )
