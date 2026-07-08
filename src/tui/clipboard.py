"""
OS-native clipboard helpers for the TUI.

Textual already wires copy/paste keys natively:
  - TextArea / Input: Ctrl+C copies the selection, Ctrl+V pastes.
  - Screen: Ctrl+C copies text selected anywhere on screen (e.g. output)
    when the focused widget has no selection of its own.

All of those routes funnel through ``App.copy_to_clipboard`` / ``App.clipboard``.
By default Textual only emits an OSC 52 escape sequence, which many terminals
ignore. We override those App methods (see app.py) so copy/paste also talk to
the real OS clipboard via the helpers below.

Linux:   wl-copy / wl-paste (Wayland) or xclip / xsel (X11)
macOS:   pbcopy / pbpaste
Windows: PowerShell Set-Clipboard / Get-Clipboard
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from textual.binding import Binding


def app_quit_bindings() -> List[tuple[str, str, str]]:
    """Quit bindings that leave the copy keys free.

    Ctrl+C is used for copy (matching terminal apps), so quit is Ctrl+Q / Escape.
    """
    return [("ctrl+q", "quit", "Quit"), ("escape", "quit", "Quit")]


def copy_paste_bindings() -> List["Binding"]:
    """OS-appropriate copy/paste shortcuts layered on Textual's native Ctrl+C/V.

    - Linux: add Ctrl+Shift+C / Ctrl+Shift+V (the terminal copy/paste
      convention). Note: many terminals grab Ctrl+Shift+C for their own copy
      and never forward it to the app, in which case plain Ctrl+C still works.
    - macOS/Windows: nothing extra — rely on native Cmd+C / Ctrl+C handling.
    """
    from textual.binding import Binding

    if platform.system() != "Linux":
        return []
    return [
        Binding("ctrl+shift+c", "copy_selection", "Copy", show=True, priority=True),
        Binding("ctrl+shift+v", "paste_clipboard", "Paste", show=False, priority=True),
    ]


def clipboard_shortcut_hint() -> str:
    if platform.system() == "Linux":
        return (
            "Select text with the mouse, then press Ctrl+Shift+C to copy "
            "(Ctrl+Shift+V or Ctrl+V to paste, Ctrl+Q to quit)."
        )
    return (
        "Select text, then press Ctrl+C / Cmd+C to copy "
        "(Ctrl+V / Cmd+V to paste, Ctrl+Q to quit)."
    )


def write_system_clipboard(text: str) -> bool:
    """Write text to the OS clipboard. Returns True on success."""
    if not text:
        return False
    env = os.environ.copy()
    system = platform.system()
    if system == "Darwin":
        return _run_input(["pbcopy"], text, env=env)
    if system == "Linux":
        if shutil.which("wl-copy"):
            if _run_input(["wl-copy"], text, env=env):
                return True
        if shutil.which("xclip"):
            if _run_input(["xclip", "-selection", "clipboard"], text, env=env):
                return True
        if shutil.which("xsel"):
            return _run_input(["xsel", "--clipboard", "--input"], text, env=env)
        return False
    if system == "Windows":
        return _write_windows_clipboard(text)
    return False


def read_clipboard() -> str:
    """Read text from the OS clipboard (empty string if unavailable)."""
    system = platform.system()
    try:
        if system == "Darwin":
            return _run_output(["pbpaste"])
        if system == "Linux":
            if shutil.which("wl-paste"):
                return _run_output(["wl-paste", "-n"])
            if shutil.which("xclip"):
                return _run_output(["xclip", "-selection", "clipboard", "-o"])
            if shutil.which("xsel"):
                return _run_output(["xsel", "--clipboard", "--output"])
        if system == "Windows":
            return _read_windows_clipboard()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def system_clipboard_available() -> bool:
    system = platform.system()
    if system == "Darwin":
        return shutil.which("pbcopy") is not None
    if system == "Linux":
        return any(shutil.which(tool) for tool in ("wl-copy", "xclip", "xsel"))
    if system == "Windows":
        return True
    return False


def _read_windows_clipboard() -> str:
    try:
        return _run_output(
            ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"]
        )
    except (OSError, subprocess.SubprocessError):
        return ""


def _write_windows_clipboard(text: str) -> bool:
    try:
        process = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        script = "$text = @'\n" f"{text}\n" "'@\n" "Set-Clipboard -Value $text\n"
        process.communicate(input=script.encode("utf-8"), timeout=3)
        return process.returncode == 0
    except (OSError, subprocess.SubprocessError):
        if shutil.which("clip"):
            return _run_input(["clip"], text, env=os.environ.copy())
        return False


def _run_output(command: List[str]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _run_input(command: List[str], text: str, *, env: dict[str, str]) -> bool:
    """Write text to a clipboard command's stdin.

    wl-copy blocks indefinitely if stdout/stderr are pipes, so use DEVNULL.
    """
    try:
        result = subprocess.run(
            command,
            input=text.encode("utf-8"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
