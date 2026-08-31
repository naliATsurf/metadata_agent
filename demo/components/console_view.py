"""Show a Rich console's output in Streamlit, exactly as the terminal shows it.

The examples render through Rich — tables, rules, colour, box drawing — and the
point of the demo is to show *that* view, not a Streamlit reconstruction of it.
A ``Console(record=True)`` captures a run and exports it as HTML, so the page can
display the real thing and keep a single renderer for terminal and browser.
"""

from __future__ import annotations

import io

from rich.console import Console
from rich.terminal_theme import MONOKAI
import streamlit as st


DEFAULT_WIDTH = 150

# Rich fills in {foreground}, {background}, and {code}; with inline styles there
# is no stylesheet to place. The <pre> scrolls on its own so a wide table never
# widens the page.
_CODE_FORMAT = (
    '<pre style="white-space:pre;overflow-x:auto;margin:0;padding:16px;'
    'border-radius:8px;font-size:12.5px;line-height:1.35;'
    'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
    'color:{foreground};background-color:{background}">{code}</pre>'
)


def recording_console(width: int = DEFAULT_WIDTH) -> Console:
    """Build a console that captures output instead of writing to a terminal.

    Args:
        width: Fixed render width in characters. Rich wraps tables to this, so
            it decides how much horizontal room the output gets.

    Returns:
        A recording :class:`~rich.console.Console`.
    """
    return Console(
        # Writes go to a throwaway buffer: the recording is what the page reads,
        # and the server's own stdout stays clean.
        file=io.StringIO(),
        record=True,
        width=width,
        force_terminal=True,
        color_system="truecolor",
        soft_wrap=False,
    )


def console_html(console: Console) -> str:
    """Export everything ``console`` captured as a self-contained HTML fragment.

    Exported against a dark terminal theme so the colours Rich chose read the
    same way here as they do in a terminal, under either Streamlit theme.
    """
    # clear=False: exporting would otherwise empty the recording, leaving
    # nothing for console_text() to return.
    return console.export_html(
        theme=MONOKAI, inline_styles=True, code_format=_CODE_FORMAT, clear=False
    )


def render_console_html(html: str) -> None:
    """Render an exported console fragment into the page."""
    st.html(html)


def console_text(console: Console) -> str:
    """Return the captured output as plain text, for download or copying."""
    return console.export_text(clear=False)
