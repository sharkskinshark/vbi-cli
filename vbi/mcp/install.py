"""Setup wizard for ``vbi mcp install``.

Registers vbi as an MCP server with Claude Code / Claude Desktop by
writing a ``mcpServers.vbi`` entry into the host's config JSON. Reuses
install.ps1's banner + skyline + braille-spinner UX so the human-facing
onboarding has the same texture as the original CLI install.

Detection priority (first existing wins; if none exist, defaults to
Claude Code's path and creates it):

  1. ``$VBI_MCP_CONFIG`` env var (test override)
  2. Claude Code CLI:        ``~/.claude.json``
  3. Claude Desktop Windows: ``%APPDATA%\\Claude\\claude_desktop_config.json``
  4. Claude Desktop macOS:   ``~/Library/Application Support/Claude/claude_desktop_config.json``
  5. Claude Desktop Linux:   ``~/.config/Claude/claude_desktop_config.json``
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

ESC = "\x1b"
RESET = f"{ESC}[0m"
DIM = f"{ESC}[2m"
GOLD = f"{ESC}[38;5;215m"
GREEN = f"{ESC}[32m"
RED = f"{ESC}[91m"

SKYLINE = "▂▅▃▆▂▇▄█▃▆▂▅▃▆▄█▂▇▆▄█▂▇▃▆▂▅▃▆▂▇▄█▃▆▂▅▆▄█▂▇▃▆▂"
BRAILLE = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

BANNER = [
    "  ██╗   ██╗██████╗ ██╗     ██████╗██╗     ██╗",
    "  ██║   ██║██╔══██╗██║    ██╔════╝██║     ██║",
    "  ██║   ██║██████╔╝██║    ██║     ██║     ██║",
    "  ╚██╗ ██╔╝██╔══██╗██║    ██║     ██║     ██║",
    "   ╚████╔╝ ██████╔╝██║    ╚██████╗███████╗██║",
    "    ╚═══╝  ╚═════╝ ╚═╝     ╚═════╝╚══════╝╚═╝",
]


def _gradient_line(line: str, max_w: int,
                   l_rgb: tuple[int, int, int],
                   r_rgb: tuple[int, int, int]) -> str:
    out: list[str] = []
    for i, ch in enumerate(line):
        if ch == ' ':
            out.append(ch)
            continue
        t = i / max(1, max_w - 1)
        r = int(l_rgb[0] + (r_rgb[0] - l_rgb[0]) * t)
        g = int(l_rgb[1] + (r_rgb[1] - l_rgb[1]) * t)
        b = int(l_rgb[2] + (r_rgb[2] - l_rgb[2]) * t)
        out.append(f"{ESC}[38;2;{r};{g};{b}m{ch}")
    out.append(RESET)
    return "".join(out)


def _ensure_utf8_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _enable_windows_ansi() -> None:
    """Enable VT100 escape processing on Windows console."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        for handle_id in (-11, -12):  # STDOUT, STDERR
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _print_banner() -> None:
    max_w = max(len(line) for line in BANNER)
    for line in BANNER:
        print(_gradient_line(line, max_w, (255, 120, 40), (255, 215, 130)))
    print(f"{DIM}  ░{'░' * 44}{RESET}")
    print("       Local-first AI usage inspection")
    print("       MCP setup wizard for Claude Code")
    print()


def _format_skyline(filled: int) -> str:
    filled = max(0, min(filled, len(SKYLINE)))
    built = SKYLINE[:filled]
    empty = "░" * (len(SKYLINE) - filled)
    return f"  {GOLD}{built}{RESET}{DIM}{empty}{RESET}"


def _format_step_line(marker: str, color: str, label: str, tail: str = "") -> str:
    suffix = f" {DIM}{tail}{RESET}" if tail else ""
    return f"  {DIM}[{RESET}{color}{marker}{RESET}{DIM}]{RESET} {label} ...{suffix}"


@dataclass
class StepRunner:
    """Coordinates the skyline row above and the per-step spinner row below."""
    total: int
    skyline_filled: int = 0
    step_index: int = 0

    def render_initial_skyline(self) -> None:
        sys.stdout.write(_format_skyline(0) + "\n")
        sys.stdout.flush()

    def run(self, label: str, action: Callable[[], None]) -> float:
        self.step_index += 1
        target = round((self.step_index / self.total) * len(SKYLINE))
        start = time.monotonic()

        result: dict[str, BaseException | None] = {"err": None}

        def _action_wrapper() -> None:
            try:
                action()
            except BaseException as exc:  # noqa: BLE001
                result["err"] = exc

        thread = threading.Thread(target=_action_wrapper, daemon=True)
        thread.start()

        # Initial spinner row directly under skyline
        sys.stdout.write(_format_step_line(BRAILLE[0], GOLD, label) + "\r")
        sys.stdout.flush()

        tick = 0
        min_ticks = 5  # show at least 0.5s of animation even for instant steps
        while thread.is_alive() or tick < min_ticks:
            time.sleep(0.1)
            tick += 1
            if self.skyline_filled < target:
                self.skyline_filled += 1

            br = BRAILLE[tick % len(BRAILLE)]
            sky = _format_skyline(self.skyline_filled)
            step = _format_step_line(br, GOLD, label)

            # Move cursor up 1 row, clear, draw skyline, back down, clear, draw step.
            sys.stdout.write(
                f"\r{ESC}[1A{ESC}[2K{sky}\n{ESC}[2K{step}\r"
            )
            sys.stdout.flush()

        thread.join()
        elapsed = time.monotonic() - start
        self.skyline_filled = target

        if result["err"] is None:
            marker, color = "✓", GREEN
        else:
            marker, color = "✗", RED

        sky = _format_skyline(self.skyline_filled)
        final = _format_step_line(marker, color, label, f"({elapsed:.1f}s)")
        sys.stdout.write(
            f"\r{ESC}[1A{ESC}[2K{sky}\n{ESC}[2K{final}\n"
        )
        sys.stdout.flush()

        if result["err"] is not None:
            raise result["err"]
        return elapsed


# ── config detection / write ──────────────────────────────────────────────────


def candidate_config_paths() -> Iterable[Path]:
    """Yield candidate Claude config paths in priority order."""
    override = os.environ.get("VBI_MCP_CONFIG")
    if override:
        yield Path(override).expanduser().resolve()
        return

    home = Path.home()
    yield home / ".claude.json"

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            yield Path(appdata) / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        yield home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:
        yield home / ".config" / "Claude" / "claude_desktop_config.json"


def detect_config_path() -> Path:
    """Return the first existing candidate, or the first candidate (to be created)."""
    candidates = list(candidate_config_paths())
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def vbi_command_path() -> str:
    """Find the absolute path to the vbi entry-point script.

    Falls back to ``vbi`` (PATH lookup) when the script can't be located.
    """
    found = shutil.which("vbi")
    if found:
        return str(Path(found).resolve())
    return "vbi"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def register_vbi_entry(config: dict, command: str, name: str = "vbi") -> dict:
    """Add or overwrite the vbi entry in config.mcpServers."""
    servers = config.setdefault("mcpServers", {})
    servers[name] = {
        "command": command,
        "args": ["mcp", "serve"],
    }
    return config


def verify_registration(path: Path, name: str = "vbi") -> bool:
    config = load_config(path)
    return isinstance(config.get("mcpServers"), dict) and name in config["mcpServers"]


# ── main ──────────────────────────────────────────────────────────────────────


def run_install(
    config_path: Path | None = None,
    name: str = "vbi",
    force: bool = False,
) -> int:
    _enable_windows_ansi()
    _ensure_utf8_stdout()

    target_path = config_path or detect_config_path()
    command = vbi_command_path()

    _print_banner()
    print(f"  config: {DIM}{target_path}{RESET}")
    print(f"  command: {DIM}{command}{RESET}")
    print(f"  server name: {DIM}{name}{RESET}")
    print()

    runner = StepRunner(total=4)
    runner.render_initial_skyline()

    config_holder: dict[str, dict] = {"config": {}}

    def step_detect() -> None:
        if not target_path.parent.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
        time.sleep(0.2)

    def step_load() -> None:
        config_holder["config"] = load_config(target_path)
        existing = config_holder["config"].get("mcpServers", {}).get(name)
        if existing and not force:
            # Existing entry — overwrite anyway, but record that we replaced it.
            config_holder["replaced"] = True  # type: ignore[assignment]
        time.sleep(0.2)

    def step_register() -> None:
        register_vbi_entry(config_holder["config"], command=command, name=name)
        write_config(target_path, config_holder["config"])
        time.sleep(0.2)

    def step_verify() -> None:
        if not verify_registration(target_path, name=name):
            raise RuntimeError(f"verification failed: {name} not found in config")
        time.sleep(0.2)

    try:
        runner.run("detect Claude config",     step_detect)
        runner.run("load existing entries",    step_load)
        runner.run(f"register '{name}' MCP",   step_register)
        runner.run("verify handshake target",  step_verify)
    except Exception as exc:  # noqa: BLE001
        print()
        print(f"  {RED}✗{RESET} install failed: {exc}")
        return 1

    print()
    print(f"  {GREEN}✓{RESET} vbi is registered with Claude Code at:")
    print(f"     {target_path}")
    print()
    print(f"  Restart Claude Code, then ask: ")
    print(f"     {GOLD}\"what AI tooling is on this machine?\"{RESET}")
    print()
    print(f"  {DIM}Claude will auto-call vbi tools (status, inventory, "
          f"map_relationships, …).{RESET}")
    print()
    return 0
