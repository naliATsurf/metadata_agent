"""
Textual-based terminal UI for Metadata Agent.
"""

from pathlib import Path
import logging
import queue
import threading

from rich.highlighter import Highlighter
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Static, TextArea
from src.tui.palette import (
    COMMAND_NAME_COLOR,
    DESCRIPTION_COLOR,
    DIALOG_COLOR,
    GLOBAL_BACKGROUND,
    INPUT_BACKGROUND,
    INPUT_WARNING_COLOR,
    NO_MATCH_COMMAND_COLOR,
    REFERENCE_BACKGROUND,
    REFERENCE_TEXT_COLOR,
    STANDARD_REFERENCE_BACKGROUND,
    STANDARD_REFERENCE_TEXT_COLOR,
    AT_SUGGESTION_SELECTION_COLOR,
    SUGGESTION_COLOR,
    TITLE_COLOR,
)
from src.standards import METADATA_STANDARDS
from src.standards import get_schema_for_standard
from src.config import (
    DEFAULT_TOPOLOGY,
    LLM_PROVIDER,
    PLANNING_TEMPERATURE,
    TUI_LOG_LEVEL,
    TUI_LOG_SUPPRESSED_LOGGERS,
    TUI_UI_VERBOSITY,
    get_model_name,
)
from src.tui.reference_session import ReferenceSessionState
from src.tui.references import (
    extract_invalid_standard_mentions,
    extract_standard_mentions,
    get_at_suggestions,
    stylize_verified_references,
)
from src.orchestrator import Orchestrator
from src.orchestrator.utils import validate_plan_dataflow, validate_plan_tool_compatibility
from src.tui.metadata_renderer import format_metadata
from src.tui.plan_renderer import format_plan
from src.tui.clipboard import (
    app_quit_bindings,
    clipboard_shortcut_hint,
    copy_paste_bindings,
    read_clipboard,
    system_clipboard_available,
    write_system_clipboard,
)
from src.context import create_context


class VerifiedReferenceHighlighter(Highlighter):
    """Highlight verified @path references in input text."""

    def __init__(self, app: "MetadataTUI") -> None:
        super().__init__()
        self.app = app

    def highlight(self, text: Text) -> None:
        self.app._stylize_verified_references(text)


class TUILogHandler(logging.Handler):
    """Forward Python logging records into the TUI event queue."""

    def __init__(
        self,
        app: "MetadataTUI",
        level: int = logging.INFO,
        ui_verbosity: str = "normal",
    ) -> None:
        super().__init__(level=level)
        self.app = app
        self.ui_verbosity = ui_verbosity

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if not message.startswith("[ui]"):
                if self.ui_verbosity == "debug":
                    formatted = self.format(record)
                    artifact_markers = (
                        "Artifacts produced:",
                        "Produced artifacts:",
                        "Final Workspace Artifacts",
                        "'artifacts':",
                        '"artifacts":',
                    )
                    if not any(marker in formatted for marker in artifact_markers):
                        self.app.ui_events.put(("log", formatted, False))
                return
            display = message[5:].strip() if message.startswith("[ui] ") else message[4:].strip()
            self.app.ui_events.put(("status", display, False))
            if self.ui_verbosity == "debug":
                self.app.ui_events.put(("log", self.format(record), False))
        except Exception:
            # Logging should never crash the UI.
            pass


class TUILogFilter(logging.Filter):
    """Filter noisy records from the TUI feed."""

    def __init__(self, suppressed_prefixes: tuple[str, ...]) -> None:
        super().__init__()
        self.suppressed_prefixes = suppressed_prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.suppressed_prefixes:
            return True
        return not record.name.startswith(self.suppressed_prefixes)


class PromptTextArea(TextArea):
    """Multiline prompt input with app-controlled submit/completion keys."""

    BINDINGS = [
        Binding("enter", "submit_or_complete", show=False, priority=True),
        Binding("up", "suggestion_up_or_cursor", show=False, priority=True),
        Binding("down", "suggestion_down_or_cursor", show=False, priority=True),
        Binding("tab", "complete_suggestion", show=False, priority=True),
    ]

    def action_submit_or_complete(self) -> None:
        if isinstance(self.app, MetadataTUI):
            self.app._handle_prompt_enter(self)

    def action_suggestion_up_or_cursor(self) -> None:
        if isinstance(self.app, MetadataTUI) and self.app._handle_prompt_navigation(self, "up"):
            return
        self.action_cursor_up()

    def action_suggestion_down_or_cursor(self) -> None:
        if isinstance(self.app, MetadataTUI) and self.app._handle_prompt_navigation(self, "down"):
            return
        self.action_cursor_down()

    def action_complete_suggestion(self) -> None:
        if isinstance(self.app, MetadataTUI):
            self.app._handle_prompt_tab(self)

    def action_paste(self) -> None:
        """Paste from the OS clipboard (Textual's default only reads the in-app copy)."""
        if self.read_only:
            return
        clipboard = read_clipboard()
        if not clipboard:
            clipboard = self.app.clipboard
        if not clipboard:
            return
        if result := self._replace_via_keyboard(clipboard, *self.selection):
            self.move_cursor(result.end_location)

    def get_line(self, line_index: int) -> Text:
        line = super().get_line(line_index)
        if isinstance(self.app, MetadataTUI):
            return self.app._stylize_verified_references(line)
        return line


class MetadataTUI(App):
    """Minimal one-request, one-answer Textual app."""
    EXIT_COMMAND_LABEL = "/quit; /exit; /bye"
    SLASH_COMMANDS = {
        "/help": "Show available commands",
        "/clear": "Clear dialog history",
        EXIT_COMMAND_LABEL: "Exit the app",
    }
    EXIT_COMMANDS = {"/quit", "/exit", "/bye"}

    CSS = (
        """
    Screen {
        padding: 0;
        background: __GLOBAL_BACKGROUND__;
    }

    #panel {
        width: 100%;
        height: 1fr;
        padding: 0;
        layout: vertical;
        overflow-y: auto;
        scrollbar-size-vertical: 0;
    }

    #feed {
        width: 100%;
        height: auto;
    }

    #title {
        text-style: bold;
        color: __TITLE_COLOR__;
        margin: 0 0 1 0;
    }

    #welcome {
        color: __DESCRIPTION_COLOR__;
        margin: 0 0 1 0;
    }

    #user_input {
        height: 3;
        margin: 0;
        padding: 1 2;
        border: none;
        background: __INPUT_BACKGROUND__;
        text-wrap: wrap;
        overflow-x: hidden;
    }

    #user_input .text-area--cursor-line {
        background: __INPUT_BACKGROUND__;
    }

    #user_input .text-area--gutter,
    #user_input .text-area--cursor-gutter {
        background: __INPUT_BACKGROUND__;
    }

    #user_input .text-area--placeholder {
        background: __INPUT_BACKGROUND__;
    }

    #command_suggestions {
        margin: 0 2 1 2;
        color: __SUGGESTION_COLOR__;
    }

    #status_line {
        height: 1;
        margin: 0 2 0 2;
        color: __SUGGESTION_COLOR__;
        text-wrap: nowrap;
        overflow: hidden;
    }

    #dialog {
        color: __DIALOG_COLOR__;
    }

    #messages {
        width: 100%;
        layout: vertical;
        height: auto;
    }

    .message {
        width: 100%;
        margin: 0;
        overflow-x: hidden;
        text-wrap: wrap;
    }

    .user-message {
        background: __INPUT_BACKGROUND__;
        padding: 1 2;
        align: center middle;
    }

    .assistant-message {
        padding: 1 2;
        align: center middle;
    }
    """
        .replace("__TITLE_COLOR__", TITLE_COLOR)
        .replace("__DESCRIPTION_COLOR__", DESCRIPTION_COLOR)
        .replace("__INPUT_BACKGROUND__", INPUT_BACKGROUND)
        .replace("__SUGGESTION_COLOR__", SUGGESTION_COLOR)
        .replace("__DIALOG_COLOR__", DIALOG_COLOR)
        .replace("__GLOBAL_BACKGROUND__", GLOBAL_BACKGROUND)
    )

    BINDINGS = [
        *copy_paste_bindings(),
        *app_quit_bindings(),
    ]
    reference_root = Path.cwd()
    standard_names = sorted(METADATA_STANDARDS.keys())
    reference_session: ReferenceSessionState
    orchestrator: Orchestrator | None = None
    backend_running: bool = False
    ui_events: "queue.Queue[tuple[str, str, bool]]"
    tui_log_handler: TUILogHandler | None = None
    at_suggestions: list[tuple[str, str]]
    at_selected_index: int
    input_history: list[str]
    input_history_browse_index: int
    _loading_input_history: bool
    INPUT_MIN_HEIGHT = 3
    INPUT_MAX_HEIGHT = 12
    STATUS_ICONS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]

    @staticmethod
    def _parse_suppressed_logger_prefixes(raw: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in raw.split(",") if part.strip())

    @staticmethod
    def _get_active_token(text: str, cursor_position: int) -> str:
        prefix = text[:cursor_position]
        if not prefix:
            return ""
        token_start = max(prefix.rfind(" "), prefix.rfind("\t"), prefix.rfind("\n")) + 1
        return prefix[token_start:]

    def _stylize_verified_references(self, rendered: Text) -> Text:
        return stylize_verified_references(
            rendered,
            root=self.reference_root,
            standard_names=self.standard_names,
            text_color=REFERENCE_TEXT_COLOR,
            background_color=REFERENCE_BACKGROUND,
            standard_text_color=STANDARD_REFERENCE_TEXT_COLOR,
            standard_background_color=STANDARD_REFERENCE_BACKGROUND,
        )

    def _render_text_with_verified_references(self, text: str, *, markup: bool = False) -> Text:
        rendered = Text.from_markup(text) if markup else Text(text)
        return self._stylize_verified_references(rendered)

    @staticmethod
    def _sanitize_status_message(message: str) -> str:
        compact = " ".join(part for part in message.replace("\n", " ").split())
        if compact.startswith("[log]"):
            compact = compact[5:].strip()
        if "[ui]" in compact:
            compact = compact.replace("[ui]", "").strip()
        return compact

    def _update_status_line(self, message: str) -> None:
        status_widget = self.query_one("#status_line", Static)
        if not self.backend_running:
            self._status_message = ""
            status_widget.update("")
            status_widget.display = False
            return
        status_widget.display = True
        self._status_message = self._sanitize_status_message(message)
        self._render_status_line()

    def _tick_status_moon(self) -> None:
        if not self.backend_running or not self._status_message:
            return
        self.status_icon_index = (self.status_icon_index + 1) % len(self.STATUS_ICONS)
        self._render_status_line()

    def _render_status_line(self) -> None:
        status_widget = self.query_one("#status_line", Static)
        icon = self.STATUS_ICONS[self.status_icon_index % len(self.STATUS_ICONS)]
        clean = self._status_message
        max_len = max(16, status_widget.size.width - 6) if status_widget.size.width else 80
        if len(clean) > max_len:
            clean = clean[: max_len - 3].rstrip() + "..."
        status_widget.update(f"{icon} {clean}")

    def _update_input_height(self, input_widget: TextArea) -> None:
        """
        Auto-grow input height based on wrapped content, so all typed text stays visible.
        Includes 2 rows for top/bottom padding in CSS.
        """
        text = input_widget.text or ""
        inner_width = max(1, input_widget.size.width - 4)  # horizontal padding is 2+2
        wrapped_lines = 0
        for line in text.splitlines() or [""]:
            wrapped_lines += max(1, (len(line) + inner_width - 1) // inner_width)
        desired_height = min(
            self.INPUT_MAX_HEIGHT,
            max(self.INPUT_MIN_HEIGHT, wrapped_lines + 2),
        )
        input_widget.styles.height = desired_height

    def _reset_at_suggestions(self) -> None:
        self.at_suggestions = []
        self.at_selected_index = 0

    @staticmethod
    def _cursor_offset(input_widget: TextArea) -> int:
        row, column = input_widget.cursor_location
        lines = input_widget.text.split("\n")
        row = max(0, min(row, len(lines) - 1))
        column = max(0, min(column, len(lines[row])))
        return sum(len(line) + 1 for line in lines[:row]) + column

    @staticmethod
    def _location_from_offset(text: str, offset: int) -> tuple[int, int]:
        offset = max(0, min(offset, len(text)))
        before_cursor = text[:offset]
        row = before_cursor.count("\n")
        column = len(before_cursor.rsplit("\n", 1)[-1])
        return row, column

    def _render_at_suggestions(self, warning_text: str) -> str:
        if not self.at_suggestions:
            return warning_text
        lines = []
        for idx, (token, kind) in enumerate(self.at_suggestions):
            is_selected = idx == self.at_selected_index
            if is_selected:
                if kind == "standard":
                    styled = (
                        f"[bold {AT_SUGGESTION_SELECTION_COLOR} on {STANDARD_REFERENCE_BACKGROUND}]"
                        f"@{token}"
                        f"[/]"
                    )
                else:
                    styled = (
                        f"[bold {AT_SUGGESTION_SELECTION_COLOR} on {REFERENCE_BACKGROUND}]"
                        f"@{token}"
                        f"[/]"
                    )
            elif kind == "standard":
                styled = (
                    f"[{STANDARD_REFERENCE_TEXT_COLOR} on {STANDARD_REFERENCE_BACKGROUND}]"
                    f"@{token}"
                    f"[/{STANDARD_REFERENCE_TEXT_COLOR} on {STANDARD_REFERENCE_BACKGROUND}]"
                )
            else:
                styled = (
                    f"[{REFERENCE_TEXT_COLOR} on {REFERENCE_BACKGROUND}]"
                    f"@{token}"
                    f"[/{REFERENCE_TEXT_COLOR} on {REFERENCE_BACKGROUND}]"
                )
            lines.append(styled)
        body = "\n".join(lines)
        return f"{warning_text}\n{body}".strip() if warning_text else body

    def _apply_selected_at_suggestion(self, input_widget: TextArea) -> bool:
        cursor_position = self._cursor_offset(input_widget)
        current_text = input_widget.text
        active_token = self._get_active_token(current_text, cursor_position)
        if not active_token.startswith("@") or not self.at_suggestions:
            return False

        selected_token = self.at_suggestions[self.at_selected_index][0]
        token_start = cursor_position - len(active_token)
        completed_token = f"@{selected_token}"
        updated_text = (
            f"{current_text[:token_start]}{completed_token}{current_text[cursor_position:]}"
        )
        new_cursor_offset = token_start + len(completed_token)
        input_widget.load_text(updated_text)
        input_widget.move_cursor(self._location_from_offset(updated_text, new_cursor_offset))
        self._update_input_height(input_widget)
        self._update_suggestions(updated_text, new_cursor_offset)
        return True

    def on_mount(self) -> None:
        self.ui_events = queue.Queue()
        self._reset_at_suggestions()
        self.input_history = []
        self.input_history_browse_index = -1
        self._loading_input_history = False
        self.status_icon_index = 0
        self._status_message = ""
        self.set_interval(0.1, self._flush_ui_events)
        self.set_interval(1.0, self._tick_status_moon)

        self.reference_session = ReferenceSessionState()
        try:
            self.orchestrator = Orchestrator(topology_name=DEFAULT_TOPOLOGY)
        except Exception:
            # Keep TUI usable even if backend initialization fails.
            self.orchestrator = None

        tui_log_level = getattr(logging, TUI_LOG_LEVEL, logging.INFO)
        root_logger = logging.getLogger()
        root_logger.setLevel(tui_log_level)
        self.tui_log_handler = TUILogHandler(
            self,
            level=tui_log_level,
            ui_verbosity=TUI_UI_VERBOSITY,
        )
        self.tui_log_handler.addFilter(
            TUILogFilter(
                self._parse_suppressed_logger_prefixes(TUI_LOG_SUPPRESSED_LOGGERS)
            )
        )
        self.tui_log_handler.setFormatter(
            logging.Formatter("[log] %(asctime)s - %(levelname)s - %(message)s", "%H:%M:%S")
        )
        root_logger.addHandler(self.tui_log_handler)

        input_widget = self.query_one("#user_input", TextArea)
        self._update_input_height(input_widget)
        input_widget.focus()
        status_widget = self.query_one("#status_line", Static)
        status_widget.update("")
        status_widget.display = False
        self.query_one("#panel", Container).scroll_home(animate=False)

    def _flush_ui_events(self) -> None:
        while True:
            try:
                kind, message, markup = self.ui_events.get_nowait()
            except queue.Empty:
                break

            if kind == "message":
                self._append_dialog(message, markup=markup)
            elif kind == "done":
                self.backend_running = False
                self._status_message = ""
                input_widget = self.query_one("#user_input", TextArea)
                suggestions_widget = self.query_one("#command_suggestions", Static)
                input_widget.disabled = False
                input_widget.display = True
                self._update_input_height(input_widget)
                suggestions_widget.display = True
                status_widget = self.query_one("#status_line", Static)
                status_widget.update("")
                status_widget.display = False
            elif kind == "log":
                self._append_dialog(message)
            elif kind == "status":
                self._update_status_line(message)

    def action_copy_selection(self) -> None:
        """Copy the current selection (focused widget or screen) to the clipboard.

        Bound to Ctrl+Shift+C on Linux; mirrors the terminal copy convention.
        """
        text = self._current_selection_text()
        if not text:
            self.notify("Select some text first, then copy.", severity="warning", timeout=2)
            return
        self.copy_to_clipboard(text)

    def action_paste_clipboard(self) -> None:
        """Paste the OS clipboard into the prompt (Ctrl+Shift+V on Linux)."""
        if self.backend_running:
            return
        focused = self.focused
        if isinstance(focused, PromptTextArea):
            focused.action_paste()

    def _current_selection_text(self) -> str:
        focused = self.focused
        selected = getattr(focused, "selected_text", "") or ""
        if selected:
            return selected
        try:
            screen_text = self.screen.get_selected_text()
        except Exception:
            screen_text = None
        return screen_text or ""

    def copy_to_clipboard(self, text: str) -> None:
        """Copy to the real OS clipboard, then fall back to Textual's OSC 52 path.

        Every native copy route (TextArea Ctrl+C, screen text-selection Ctrl+C)
        funnels through here, so overriding it makes copy work everywhere even
        when the terminal ignores OSC 52.
        """
        # Keep Textual's internal clipboard and OSC 52 sequence for paste/SSH.
        super().copy_to_clipboard(text)
        if not text:
            return

        if write_system_clipboard(text):
            preview = text.replace("\n", " ").strip()
            if len(preview) > 40:
                preview = preview[:40] + "..."
            self.notify(f"Copied: {preview}", timeout=2)
        elif not system_clipboard_available():
            self.notify(
                "Copied via terminal escape. For reliable copy install "
                "wl-clipboard (Wayland) or xclip/xsel (X11).",
                severity="warning",
                timeout=5,
            )

    def _dialog_content_width(self) -> int | None:
        panel = self.query_one("#panel", Container)
        if panel.size.width > 0:
            return max(40, panel.size.width - 6)
        return None

    def _run_pipeline_worker(
        self,
        *,
        source_files: list[str],
        chosen_standard: str,
        render_width: int | None = None,
    ) -> None:
        if self.orchestrator is None:
            self.ui_events.put(("message", "[red]Backend is not initialized. Unable to run pipeline.[/red]", True))
            self.ui_events.put(("done", "", False))
            return

        try:
            standard_prompt = METADATA_STANDARDS[chosen_standard]
            output_schema = get_schema_for_standard(chosen_standard)
            if not output_schema:
                self.ui_events.put(
                    (
                        "message",
                        f"[yellow]Structured output schema not found for @{chosen_standard}; using free-form output.[/yellow]",
                        True,
                    )
                )
            context = create_context(source_files, name="tui_context")

            self.ui_events.put(("status", "planning", False))
            plan = self.orchestrator.generate_plan(
                context=context,
                metadata_standard=standard_prompt,
            )
            if plan is None:
                self.ui_events.put(("message", "[red]Plan generation failed.[/red]", True))
                self.ui_events.put(("done", "", False))
                return

            self.ui_events.put(
                (
                    "message",
                    format_plan(plan, width=render_width),
                    True,
                )
            )

            if not self.orchestrator._validate_plan(plan, context):
                plan_dict = plan.to_dict_list()
                dataflow_ok, dataflow_msg = validate_plan_dataflow(plan_dict)
                if not dataflow_ok:
                    self.ui_events.put(
                        (
                            "message",
                            f"[red]Generated plan failed validation:[/red] {dataflow_msg}",
                            True,
                        )
                    )
                else:
                    allowed_players = set(self.orchestrator._get_effective_player_pool(context))
                    tools_ok, tools_msg = validate_plan_tool_compatibility(
                        plan=plan_dict,
                        context_type=context.context_type,
                        allowed_players=allowed_players,
                    )
                    self.ui_events.put(
                        (
                            "message",
                            f"[red]Generated plan failed validation:[/red] {tools_msg if not tools_ok else 'Unknown validation error.'}",
                            True,
                        )
                    )
                self.ui_events.put(("done", "", False))
                return

            result = self.orchestrator.execute_plan(
                plan=plan,
                context=context,
                metadata_standard=standard_prompt,
                metadata_standard_name=chosen_standard,
            )
            if result is None:
                self.ui_events.put(("message", "[red]Pipeline run failed: no result returned.[/red]", True))
            elif result.final_metadata:
                self.ui_events.put(("message", f"Pipeline completed with @{chosen_standard}.", False))
                self.ui_events.put(
                    (
                        "message",
                        format_metadata(result.final_metadata, width=render_width),
                        True,
                    )
                )
            else:
                self.ui_events.put(
                    (
                        "message",
                        f"Pipeline completed with @{chosen_standard}, but no final metadata was produced.",
                        False,
                    )
                )
        except Exception as exc:
            self.ui_events.put(("message", f"[red]Pipeline run failed: {exc}[/red]", True))
        finally:
            self.ui_events.put(("done", "", False))

    def compose(self) -> ComposeResult:
        yield Container(
            Vertical(
                Static("Metadata Agent", id="title"),
                Static(
                    "This tool works with dataset and table metadata. "
                    "Describe your need, or type /help for available commands. "
                    + clipboard_shortcut_hint(),
                    id="welcome",
                ),
                Vertical(id="messages"),
                Static("", id="status_line"),
                PromptTextArea(
                    placeholder="Type your request (or /help) and press Enter...",
                    soft_wrap=True,
                    show_line_numbers=False,
                    compact=True,
                    highlight_cursor_line=False,
                    id="user_input",
                ),
                Static("", id="command_suggestions"),
                id="feed",
            ),
            id="panel",
        )

    def _append_dialog(self, text: str, *, is_user: bool = False, markup: bool = False) -> None:
        messages = self.query_one("#messages", Vertical)
        classes = "message user-message" if is_user else "message assistant-message"
        messages.mount(
            Static(
                self._render_text_with_verified_references(text, markup=markup),
                classes=classes,
            )
        )
        self.query_one("#panel", Container).scroll_end(animate=False)

    def _update_suggestions(self, current_text: str, cursor_position: int) -> None:
        suggestions_widget = self.query_one("#command_suggestions", Static)
        warning_text = ""
        standard_mentions = extract_standard_mentions(current_text, self.standard_names)
        if len(standard_mentions) > 1:
            warning_text = (
                f"[{INPUT_WARNING_COLOR}]You should only provide one standard.[/{INPUT_WARNING_COLOR}]"
            )

        active_token = self._get_active_token(current_text, cursor_position)
        if not active_token:
            self._reset_at_suggestions()
            suggestions_widget.update(warning_text)
            return

        if active_token.startswith("/") and current_text[:cursor_position].lstrip() == active_token:
            self._reset_at_suggestions()
            matches = [
                (command, description)
                for command, description in self.SLASH_COMMANDS.items()
                if (
                    command.startswith(active_token)
                    or (
                        command == self.EXIT_COMMAND_LABEL
                        and any(exit_cmd.startswith(active_token) for exit_cmd in self.EXIT_COMMANDS)
                    )
                )
            ]

            if not matches:
                suggestion_text = (
                    f"[{NO_MATCH_COMMAND_COLOR}]No matching commands[/{NO_MATCH_COMMAND_COLOR}]"
                )
                suggestions_widget.update(
                    f"{warning_text}\n{suggestion_text}".strip() if warning_text else suggestion_text
                )
                return

            suggestions_text = "\n".join(
                f"[bold {COMMAND_NAME_COLOR}]{command}[/bold {COMMAND_NAME_COLOR}] - {description}"
                for command, description in matches
            )
            suggestions_widget.update(
                f"{warning_text}\n{suggestions_text}".strip() if warning_text else suggestions_text
            )
            return

        if active_token.startswith("@"):
            previous_selection = (
                self.at_suggestions[self.at_selected_index][0]
                if self.at_suggestions and self.at_selected_index < len(self.at_suggestions)
                else None
            )
            raw_suggestions = get_at_suggestions(
                active_token,
                root=self.reference_root,
                standard_names=self.standard_names,
            )
            # Do not suggest references that are already present in the prompt.
            suggestions = [
                item
                for item in raw_suggestions
                if f"@{item.token}" not in current_text
            ]

            if not suggestions:
                self._reset_at_suggestions()
                # If matches exist but were filtered because they are already in input,
                # avoid showing a misleading "No matching..." message.
                if raw_suggestions:
                    suggestions_widget.update(warning_text)
                else:
                    suggestion_text = (
                        f"[{NO_MATCH_COMMAND_COLOR}]No matching files or standards[/{NO_MATCH_COMMAND_COLOR}]"
                    )
                    suggestions_widget.update(
                        f"{warning_text}\n{suggestion_text}".strip() if warning_text else suggestion_text
                    )
                return

            self.at_suggestions = [(item.token, item.kind) for item in suggestions]
            if previous_selection:
                matching_indexes = [
                    idx for idx, (token, _) in enumerate(self.at_suggestions) if token == previous_selection
                ]
                self.at_selected_index = matching_indexes[0] if matching_indexes else 0
            elif self.at_selected_index >= len(self.at_suggestions):
                self.at_selected_index = 0

            suggestions_widget.update(self._render_at_suggestions(warning_text))
            return

        self._reset_at_suggestions()
        suggestions_widget.update(warning_text)

    def _handle_command(self, command_text: str) -> None:
        if command_text == "/help":
            self._append_dialog(
                "[bold cyan]Available commands[/bold cyan]\n"
                "- /help: Show available commands\n"
                "- /clear: Clear dialog history\n"
                "- /quit (/exit, /bye): Exit the app",
                markup=True,
            )
            return
        if command_text == "/clear":
            messages = self.query_one("#messages", Vertical)
            messages.remove_children()
            self.reference_session.clear_current()
            self.input_history.clear()
            self.input_history_browse_index = -1
            return
        if command_text in self.EXIT_COMMANDS:
            self.exit()
            return
        self._append_dialog(
            f"[yellow]Unknown command:[/yellow] {command_text}",
            markup=True,
        )

    def _submit_current_input(self, input_widget: TextArea) -> None:
        if self.backend_running:
            self._append_dialog(
                "[red]Pipeline is already running. Please wait for completion.[/red]",
                markup=True,
            )
            return

        user_text = input_widget.text.strip()
        if user_text:
            self.input_history.append(user_text)
            self.input_history_browse_index = -1
        input_widget.load_text("")
        self._update_input_height(input_widget)
        self._update_suggestions("", 0)
        self.reference_session.clear_current()

        if not user_text:
            return

        if user_text.startswith("/"):
            self._handle_command(user_text)
            return

        referenced_items = self.reference_session.commit_current_input(
            user_text,
            self.reference_root,
        )
        referenced_files = [ref for ref in referenced_items if ref.is_file]
        referenced_standards = extract_standard_mentions(user_text, self.standard_names)
        invalid_standards = extract_invalid_standard_mentions(
            user_text,
            standard_names=self.standard_names,
            root=self.reference_root,
        )

        self._append_dialog(user_text, is_user=True)

        if invalid_standards:
            self._append_dialog(
                (
                    f"[red]Invalid @standard reference(s): {', '.join(f'@{s}' for s in invalid_standards)}. "
                    "Please use a valid standard name.[/red]"
                ),
                markup=True,
            )
            return

        if not referenced_files:
            self._append_dialog(
                "[red]Please include at least one valid @file reference to run this tool.[/red]",
                markup=True,
            )
            return

        if referenced_standards:
            chosen_standard = referenced_standards[0]
            if len(referenced_standards) > 1:
                self._append_dialog(
                    (
                        f"[red]Multiple standards detected ({', '.join(referenced_standards)}). "
                        f"Using @{chosen_standard}.[/red]"
                    ),
                    markup=True,
                )

            source = [str(ref.absolute_path) for ref in referenced_files]
            self.backend_running = True
            input_widget.disabled = True
            input_widget.display = False
            self.query_one("#command_suggestions", Static).display = False
            self._append_dialog(f"Running pipeline with @{chosen_standard}...")
            threading.Thread(
                target=self._run_pipeline_worker,
                kwargs={
                    "source_files": source,
                    "chosen_standard": chosen_standard,
                    "render_width": self._dialog_content_width(),
                },
                daemon=True,
            ).start()
            return

        # No standard provided yet: return a dummy confirmation for now.
        file_summary = ", ".join(ref.path_name for ref in referenced_files)
        self._append_dialog(
            f"Captured valid file context: {file_summary}. No @standard provided yet.",
        )

    def _set_prompt_text(self, input_widget: TextArea, text: str) -> None:
        self._loading_input_history = True
        try:
            input_widget.load_text(text)
            input_widget.move_cursor(self._location_from_offset(text, len(text)))
            self.reference_session.update_from_input(text, self.reference_root)
            self._update_input_height(input_widget)
            self._update_suggestions(text, len(text))
        finally:
            self._loading_input_history = False

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "user_input":
            return
        if not self._loading_input_history:
            self.input_history_browse_index = -1
        cursor_position = self._cursor_offset(event.text_area)
        self.reference_session.update_from_input(event.text_area.text, self.reference_root)
        self._update_input_height(event.text_area)
        self._update_suggestions(event.text_area.text, cursor_position)

    def _handle_prompt_enter(self, input_widget: TextArea) -> bool:
        current_text = input_widget.text
        cursor_position = self._cursor_offset(input_widget)
        active_token = self._get_active_token(current_text, cursor_position)
        is_at_context = active_token.startswith("@")

        if is_at_context and self.at_suggestions:
            selected_token = self.at_suggestions[self.at_selected_index][0]
            completed_token = f"@{selected_token}"
            if completed_token in current_text:
                return True
            if self._apply_selected_at_suggestion(input_widget):
                return True

        self._submit_current_input(input_widget)
        return True

    def _handle_prompt_navigation(self, input_widget: TextArea, key: str) -> bool:
        current_text = input_widget.text
        cursor_position = self._cursor_offset(input_widget)
        active_token = self._get_active_token(current_text, cursor_position)
        if active_token.startswith("@") and self.at_suggestions:
            delta = -1 if key == "up" else 1
            self.at_selected_index = (self.at_selected_index + delta) % len(self.at_suggestions)
            self._update_suggestions(current_text, cursor_position)
            return True

        if current_text.strip() and self.input_history_browse_index < 0:
            return False

        if not self.input_history:
            return False

        if key == "up":
            if self.input_history_browse_index < 0:
                self.input_history_browse_index = len(self.input_history) - 1
            elif self.input_history_browse_index > 0:
                self.input_history_browse_index -= 1
        else:
            if self.input_history_browse_index < 0:
                return False
            if self.input_history_browse_index >= len(self.input_history) - 1:
                self.input_history_browse_index = -1
                self._set_prompt_text(input_widget, "")
                return True
            self.input_history_browse_index += 1

        self._set_prompt_text(
            input_widget,
            self.input_history[self.input_history_browse_index],
        )
        return True

    def _handle_prompt_tab(self, input_widget: TextArea) -> bool:
        current_text = input_widget.text
        cursor_position = self._cursor_offset(input_widget)
        active_token = self._get_active_token(current_text, cursor_position)
        is_at_context = active_token.startswith("@")
        if is_at_context and self.at_suggestions and self._apply_selected_at_suggestion(input_widget):
            return True

        suggestions = get_at_suggestions(
            active_token,
            root=self.reference_root,
            standard_names=self.standard_names,
        )
        matches = [item.token for item in suggestions]

        if len(matches) != 1:
            return False

        token_start = cursor_position - len(active_token)
        completed_token = f"@{matches[0]}"
        updated_text = (
            f"{current_text[:token_start]}{completed_token}{current_text[cursor_position:]}"
        )
        new_cursor_offset = token_start + len(completed_token)
        input_widget.load_text(updated_text)
        input_widget.move_cursor(self._location_from_offset(updated_text, new_cursor_offset))
        self._update_input_height(input_widget)
        self._update_suggestions(updated_text, new_cursor_offset)
        return True


def run_tui() -> None:
    """Run the Textual TUI app."""
    MetadataTUI().run()
