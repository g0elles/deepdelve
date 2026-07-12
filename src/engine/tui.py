from datetime import datetime
from textual import work, on
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, Static, Collapsible, RichLog, Button
from textual.containers import VerticalScroll, Horizontal, Vertical
from rich.markdown import Markdown
from engine.orchestrator import create_local_agent, reset_session, delegation_depth_ctx, build_quota_pool
import engine.orchestrator as orchestrator_module
import asyncio
import json
import config
from agent_framework import Message, Content
from textual import events
import os
import re
import time
import uuid
import sys
import argparse
from pathlib import Path
from tools import tool_quotas_ctx, get_workspace_files, get_workspace_file_content
from utils.run_state import reset_fetched_urls, get_fetched_urls, RunState, run_state_ctx

AGENT_NAME = config.APP_TITLE
AGENT_DESCRIPTION = config.APP_DESCRIPTION

# ponytail: pyfiglet rendered this once (font="doom"); regenerate and re-paste if APP_TITLE changes.
BANNER_ASCII = r"""
______               ______     _
|  _  \              |  _  \   | |
| | | |___  ___ _ __ | | | |___| |_   _____
| | | / _ \/ _ \ '_ \| | | / _ \ \ \ / / _ \
| |/ /  __/  __/ |_) | |/ /  __/ |\ V /  __/
|___/ \___|\___| .__/|___/ \___|_| \_/ \___|
               | |
               |_|
"""

_session_events = []
_current_call_by_source = {}
_current_text_by_source = {}
_current_session_id = str(uuid.uuid4())

def _write_log():
    if not config.cfg["settings"].get("enable_session_persistence", False):
        return

    log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"session_{_current_session_id}.json"

    payload = {
        "timestamp": datetime.now().isoformat(),
        "ui_events": _session_events,
        "agent_session": None,
        "session_id": _current_session_id
    }

    if orchestrator_module._session:
        try:
            payload["agent_session"] = orchestrator_module._session.to_dict()
        except Exception:
            pass

    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass

def log_prompt(prompt: str):
    global _session_events, _current_call_by_source, _current_text_by_source
    _session_events.append({
        "timestamp": datetime.now().isoformat(),
        "source": "User",
        "type": "prompt",
        "data": {"text": prompt}
    })
    _current_call_by_source.clear()
    _current_text_by_source.clear()
    _write_log()

def log_stream_content(source: str, content_type: str, raw_data_dict: dict, depth: int = None):
    global _session_events, _current_call_by_source, _current_text_by_source
    if depth is None:
        depth = delegation_depth_ctx.get()

    if content_type == "text" or content_type == "reasoning":
        text_val = raw_data_dict.get("text")
        if not text_val: return
        _current_call_by_source[source] = None

        idx = _current_text_by_source.get(source)
        if idx is not None and idx < len(_session_events) and _session_events[idx]["type"] == content_type:
            _session_events[idx]["data"]["text"] += text_val
        else:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "type": content_type,
                "data": {"text": text_val},
                "depth": depth
            }
            _session_events.append(entry)
            _current_text_by_source[source] = len(_session_events) - 1

    elif content_type == "function_call":
        _current_text_by_source[source] = None

        call_id = raw_data_dict.get("call_id")
        name = raw_data_dict.get("name")
        arguments = raw_data_dict.get("arguments", "")

        if call_id:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "type": "function_call",
                "data": {
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments
                },
                "depth": depth
            }
            _session_events.append(entry)
            _current_call_by_source[source] = len(_session_events) - 1
        else:
            idx = _current_call_by_source.get(source)
            if idx is not None and idx < len(_session_events):
                if arguments:
                    _session_events[idx]["data"]["arguments"] += arguments

    elif content_type == "function_result":
        _current_text_by_source[source] = None
        _current_call_by_source[source] = None

        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "type": "function_result",
            "data": raw_data_dict,
            "depth": depth
        }
        _session_events.append(entry)

    elif content_type in ("subagent_start", "subagent_end"):
        _current_text_by_source[source] = None
        _current_call_by_source[source] = None

        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "type": content_type,
            "data": raw_data_dict,
            "depth": depth
        }
        _session_events.append(entry)

    _write_log()

class PromptInput(Input):
    """An Input that maintains command history navigated with Up/Down arrows."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index: int = -1
        self._last_paste_text: str | None = None
        self._last_paste_time: float = 0.0

    def _on_paste(self, event: events.Paste) -> None:
        # Textual's base Input._on_paste does `event.text.splitlines()[0]` — it silently keeps
        # only the FIRST line of a paste and discards the rest, with no error or truncation
        # notice. Since this box is a single-line query input (not a multi-line editor), a
        # multi-line paste is flattened into one line instead of losing everything after line 1.
        #
        # Debounce guard: some terminal/tmux paste paths deliver the same paste twice in quick
        # succession, and the second delivery isn't always byte-identical — confirmed live, a
        # large pasted prompt showed up with the FULL text followed by a truncated repeat of its
        # own opening (i.e. the second delivery was a strict prefix of the first, not an exact
        # duplicate — consistent with the terminal re-sending a paste that got interrupted
        # mid-stream). insert_text_at_cursor is not idempotent, so a double-fire duplicates
        # content instead of being a harmless no-op. Treat two pastes within 0.5s where one is a
        # prefix of the other as the same event delivered twice, and skip the second.
        if event.text:
            import time
            now = time.monotonic()
            prev = self._last_paste_text
            is_redelivery = (
                prev is not None and (now - self._last_paste_time) < 0.5
                and (event.text == prev or prev.startswith(event.text) or event.text.startswith(prev))
            )
            if is_redelivery:
                event.stop()
                return
            self._last_paste_text = event.text
            self._last_paste_time = now

            text = " ".join(line.strip() for line in event.text.splitlines() if line.strip())
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(text)
            else:
                self.replace(text, *selection)
        event.stop()

    def on_key(self, event: events.Key) -> None:
        try:
            opt_list = self.app.query_one("#command-list", OptionList)
            if opt_list.display:
                if event.key == "up":
                    if opt_list.highlighted is not None and opt_list.highlighted > 0:
                        opt_list.highlighted -= 1
                    event.prevent_default()
                    return
                elif event.key == "down":
                    if opt_list.highlighted is None:
                        opt_list.highlighted = 0
                    elif opt_list.highlighted < opt_list.option_count - 1:
                        opt_list.highlighted += 1
                    event.prevent_default()
                    return
                elif event.key == "tab":
                    if opt_list.highlighted is not None:
                        opt = opt_list.get_option_at_index(opt_list.highlighted)
                        cmd = str(opt.prompt).split(" - ")[0]
                        self.value = cmd
                        self.cursor_position = len(cmd)
                    event.prevent_default()
                    return
                elif event.key == "enter":
                    if opt_list.highlighted is not None:
                        opt = opt_list.get_option_at_index(opt_list.highlighted)
                        cmd = str(opt.prompt).split(" - ")[0]
                        self.value = cmd
                        self.cursor_position = len(cmd)
                    # allow enter to propagate
        except Exception:
            pass

        if event.key == "up":
            if self._history and self._history_index > 0:
                self._history_index -= 1
                self.value = self._history[self._history_index]
            elif self._history and self._history_index == -1:
                self._history_index = len(self._history) - 1
                self.value = self._history[self._history_index]
            event.prevent_default()
        elif event.key == "down":
            if self._history_index != -1 and self._history_index < len(self._history) - 1:
                self._history_index += 1
                self.value = self._history[self._history_index]
            elif self._history_index == len(self._history) - 1:
                self._history_index = -1
                self.value = ""
            event.prevent_default()

    def record_history(self, val: str) -> None:
        if val:
            if not self._history or self._history[-1] != val:
                self._history.append(val)
        self._history_index = -1

class ApprovalWidget(Static):
    def __init__(self, action: str, agent_name: str = "Agent", arguments: str = ""):
        super().__init__(classes="agent-bubble")
        self.action = action
        self.agent_name = agent_name
        self.arguments = arguments
        self.approved = False
        self.event = asyncio.Event()

    def compose(self) -> ComposeResult:
        args_str = ""
        if self.arguments:
            if isinstance(self.arguments, str):
                args_str = self.arguments
            else:
                import json
                try:
                    args_str = json.dumps(self.arguments, indent=2)
                except Exception:
                    args_str = str(self.arguments)

        md_text = f"**Tool approval required:** `[{self.agent_name}] {self.action}`"
        if args_str:
            md_text += f"\n```json\n{args_str}\n```"

        yield Static(Markdown(md_text))
        with Horizontal(classes="approval-buttons"):
            yield Button("Approve", id="approve", variant="success")
            yield Button("Deny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.approved = (event.button.id == "approve")
        self.event.set()
        self.remove()

class ThinkingWidget(Collapsible):
    """A collapsible widget that streams reasoning tokens in real time."""
    DOTS_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self):
        self._content = Static("", classes="thinking-text")
        self._buffer = ""
        self._frame_idx = 0
        super().__init__(self._content, title="💭 Thinking...", classes="thinking-collapsible")

    def on_mount(self) -> None:
        self.collapsed = False
        self._timer = self.set_interval(0.1, self._animate)

    def _animate(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self.DOTS_FRAMES)
        if not self.collapsed:
            self.title = f"💭 Thinking {self.DOTS_FRAMES[self._frame_idx]}"

    def append(self, text: str) -> None:
        self._buffer += text
        self._content.update(self._buffer)

    def finish(self) -> None:
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.title = "💭 Thinking (done)"
        self.collapsed = True


class AgentMessageWidget(Static):
    def __init__(self, author: str):
        super().__init__(Markdown(f"**{author}:** "), classes="agent-bubble")
        self.author = author
        self.text = ""

    def append_text(self, new_text: str):
        self.text += new_text
        self.update(Markdown(f"**{self.author}:**\n{self.text}"))

def _copy_to_system_clipboard(text: str) -> bool:
    """Best-effort direct clipboard write via system tools (Wayland/X11), tried before falling
    back to Textual's OSC52-based App.copy_to_clipboard(). OSC52 depends on the terminal emulator
    (and any multiplexer in between) actually implementing the escape sequence — when it doesn't,
    the write silently no-ops with no exception, so the UI can report success while nothing
    actually reaches the clipboard. Returns True only if a real clipboard tool was found and ran
    without error."""
    import subprocess
    import shutil
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), timeout=2, check=True)
                return True
            except Exception:
                continue
    return False


class UserMessageWidget(Static):
    def __init__(self, query: str):
        super().__init__(Markdown(f"**User (Click to Copy):**\n{query}"), classes="user-bubble")
        self.query = query

    def on_click(self) -> None:
        try:
            if _copy_to_system_clipboard(self.query):
                self.app.notify("Copied prompt to clipboard!")
            else:
                self.app.copy_to_clipboard(self.query)
                self.app.notify(
                    "Copied via terminal escape sequence (OSC52) — no xclip/wl-copy found. "
                    "If it didn't actually land in your clipboard, install xclip (X11) or "
                    "wl-clipboard (Wayland)."
                )
        except Exception as e:
            self.app.notify(f"Copy failed: {e}", severity="error")

class ProcessingWidget(Static):
    """Widget to display a processing indicator before the first response."""
    DOTS_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, agent_name: str = "Agent"):
        super().__init__("", classes="agent-bubble")
        self.agent_name = agent_name
        self._frame = 0
        self._start_time = datetime.now()

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._animate_dots)
        self._animate_dots()

    def _animate_dots(self) -> None:
        self._frame = (self._frame + 1) % len(self.DOTS_FRAMES)
        elapsed = datetime.now() - self._start_time
        self.update(f"[b]{self.agent_name}:[/b] {self.DOTS_FRAMES[self._frame]} ({elapsed.total_seconds():.1f}s)")

    def stop(self) -> None:
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.remove()

    def mark_stopped(self) -> None:
        if hasattr(self, "_timer"):
            self._timer.stop()
        elapsed = datetime.now() - self._start_time
        self.update(f"[b]{self.agent_name}:[/b] \N{OCTAGONAL SIGN} Stopped ({elapsed.total_seconds():.1f}s)")

    def mark_error(self, error_msg: str) -> None:
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.update(f"[b]{self.agent_name}:[/b] [red]\N{CROSS MARK} Error: {error_msg}[/red]")

class ToolCallWidget(Collapsible):
    """Widget to display a tool call and its result."""
    DOTS_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, name: str, call_id: str, is_subagent: bool = False, agent_name: str = None):
        self.call_id = call_id
        self.tool_name = name
        self.is_subagent = is_subagent
        self.agent_name = agent_name
        self.args_text = ""
        self.result_text = ""
        self._done = False
        self._frame = 0
        self._start_time = datetime.now()

        self.args_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
        self.args_log.border_title = "Arguments"

        self.result_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
        self.result_log.border_title = "Result"

        agent_label = self.agent_name if self.agent_name else ("Sub-Agent" if is_subagent else "Agent")
        title = f"\N{HAMMER AND WRENCH} \\[{agent_label}] {name} {self.DOTS_FRAMES[0]}"
        css_class = "subagent-tool" if is_subagent else "orchestrator-tool"
        super().__init__(
            self.args_log,
            self.result_log,
            title=title,
            classes=css_class,
            collapsed=True
        )

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._animate_dots)

    def _animate_dots(self) -> None:
        if self._done:
            self._timer.stop()
            return
        self._frame = (self._frame + 1) % len(self.DOTS_FRAMES)
        elapsed = datetime.now() - self._start_time
        agent_label = self.agent_name if self.agent_name else ("Sub-Agent" if self.is_subagent else "Agent")
        self.title = f"\N{HAMMER AND WRENCH} \\[{agent_label}] {self.tool_name} {self.DOTS_FRAMES[self._frame]} ({elapsed.total_seconds():.1f}s)"

    def append_args(self, text: str):
        self.args_text += text
        self.args_log.clear()
        self.args_log.write(self.args_text)

    def set_result(self, text: str):
        self.result_text = text
        self.result_log.clear()
        self.result_log.write(self.result_text)
        self._done = True
        elapsed = datetime.now() - self._start_time
        agent_label = self.agent_name if self.agent_name else ("Sub-Agent" if self.is_subagent else "Agent")
        self.title = f"\N{HAMMER AND WRENCH} \\[{agent_label}] {self.tool_name} \N{WHITE HEAVY CHECK MARK} ({elapsed.total_seconds():.1f}s)"

    def mark_stopped(self):
        self._done = True
        elapsed = datetime.now() - self._start_time
        agent_label = self.agent_name if self.agent_name else ("Sub-Agent" if self.is_subagent else "Agent")
        self.title = f"\N{HAMMER AND WRENCH} \\[{agent_label}] {self.tool_name} \N{OCTAGONAL SIGN} ({elapsed.total_seconds():.1f}s)"

class BasicTuiAgent(App):
    CSS = """
    #chat-container { height: 1fr; scrollbar-color: green; }
    .user-bubble { margin: 1 2; padding: 1; background: #333333; color: white; text-align: right; }
    .user-bubble:hover { background: #444444; color: #aaffaa; }
    .agent-bubble { margin: 1 2; padding: 1; color: white; }
    .orchestrator-tool { border-left: vkey blue; margin: 0 2 1 2; }
    .subagent-tool { border-left: vkey purple; margin: 0 2 1 6; }
    .thinking-collapsible { margin: 0 2 1 2; border-left: vkey #555555; }
    .thinking-collapsible CollapsibleTitle { color: #777777; text-style: italic; }
    .thinking-collapsible Contents { padding: 0; margin: 0; }
    .thinking-collapsible .thinking-text { color: #888888; margin: 0 1; height: auto; }
    RichLog { height: auto; max-height: 20; margin: 0 1; border: solid #333; }
    .approval-buttons { height: auto; margin-top: 1; margin-bottom: 1; }
    .file-viewer-wrapper { border: solid #4CAF50; margin: 1 2; max-height: 25; height: auto; overflow: hidden; background: #222222; }
    .file-viewer-collapsible { width: 1fr; height: auto; }
    .file-viewer-collapsible CollapsibleTitle { color: #81C784; text-style: bold; }
    .file-viewer-inner { position: relative; height: auto; }
    .title-copy-btn { dock: right; width: auto; height: 1; min-width: 3; border: none; background: transparent; color: #888888; padding: 0; margin: 0 1 0 0; }
    .title-copy-btn:hover { color: white; background: transparent; }
    #command-list { height: auto; max-height: 15; padding: 0 1; }
    """

    SLASH_COMMANDS = [("/stop", "Stop execution"), ("/new", "New conversation"), ("/exit", "Quit app"), ("/toggle_thinking", "Toggle reasoning trace capability"), ("/toggle_persistence", "Toggle session history saving"), ("/config", "Show current configuration"), ("/files", "Browse memory workspace files"), ("/sessions", "List saved sessions"), ("/resume", "Resume a saved session")]
    def __init__(self, builder, session_to_resume: str = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.builder = builder
        self.session_to_resume = session_to_resume
        # Pre-research intake state: _pending_clarify holds the original query while the user
        # answers the intake questions; _clarify_done means this conversation's research already
        # started, so follow-up turns skip intake entirely.
        self._pending_clarify = None
        self._clarify_done = False
        # Conversation-scoped run continuity: follow-up turns reuse the first run's workspace,
        # fetched-URL record, and _run_state.json instead of minting a fresh isolated folder per
        # message — otherwise "expand on niche 2" runs blind in an empty workspace, unable to see
        # the sources the conversation just gathered. Reset by /new.
        self._active_run_dir = None
        self._conv_fetched = None
        self._conv_run_state = None

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="chat-container")
        opt_list = OptionList(id="command-list")
        opt_list.display = False
        yield opt_list
        yield PromptInput(id="prompt-input", placeholder="Type a message or /command...")

    def _banner_widget(self) -> Static:
        ascii_art = BANNER_ASCII

        endpoint = config.cfg["api"]["openai_base_url"]
        model = config.cfg["api"]["openai_model"]
        thinking = "ON" if config.cfg["settings"]["enable_thinking"] else "OFF"
        thinking_color = "green" if config.cfg["settings"]["enable_thinking"] else "red"
        memory = "ON" if config.cfg["settings"].get("enable_conversational_memory", False) else "OFF"
        memory_color = "green" if config.cfg["settings"].get("enable_conversational_memory", False) else "red"
        persistence_val = "ON" if config.cfg["settings"].get("enable_session_persistence", False) else "OFF"
        persistence_color = "green" if config.cfg["settings"].get("enable_session_persistence", False) else "red"

        config_path = getattr(config, "_CONFIG_PATH", "Unknown")
        workspace_type = config.cfg.get("settings", {}).get("workspace", {}).get("type", "memory")
        workspace_dir = config.cfg.get("settings", {}).get("workspace", {}).get("dir", ".")
        workspace_disp = f"Disk ({workspace_dir})" if workspace_type == "disk" else "In-Memory"

        status_line = f"  [dim]Config Loaded:[/dim] [bright_black]{config_path}[/bright_black]  [dim]Workspace:[/dim] [yellow]{workspace_disp}[/yellow]\n  [dim]Endpoint:[/dim] [cyan]{endpoint}[/cyan]  [dim]Model:[/dim] [cyan]{model}[/cyan]  [dim]Thinking:[/dim] [{thinking_color}]{thinking}[/{thinking_color}]  [dim]Conv Memory:[/dim] [{memory_color}]{memory}[/{memory_color}]\n  [dim]Session ID:[/dim] [bright_black]{_current_session_id}[/bright_black]  [dim]Persistence:[/dim] [{persistence_color}]{persistence_val}[/{persistence_color}]"

        auto_approve_warning = "\n\n  [bold red blink]⚠️ AUTO-APPROVE OVERRIDE ACTIVE - ALL INTERACTIVE SAFEGUARDS BYPASSED[/bold red blink]" if getattr(config, 'AUTO_APPROVE', False) else ""

        return Static(
            f"[bold green]{ascii_art}[/bold green]\n"
            f"  [bold green]{AGENT_DESCRIPTION}[/bold green]\n{status_line}{auto_approve_warning}\n\n"
            f"  [dim]Ready! Type a query or use / for commands.[/dim]\n",
            classes="agent-bubble", id="banner"
        )

    async def on_mount(self) -> None:
        self._is_agent_running = False
        self._file_picker_active = False
        self._filtered_cmds = []
        chat = self.query_one("#chat-container", VerticalScroll)
        chat.mount(self._banner_widget())
        self.query_one("#prompt-input", PromptInput).focus()

        if getattr(self, "session_to_resume", None):
            await self._load_session_by_id(self.session_to_resume)

    def on_input_changed(self, event: Input.Changed) -> None:
        if getattr(self, "_file_picker_active", False) or getattr(self, "_session_picker_active", False):
            return
        val = event.value
        opt_list = self.query_one("#command-list", OptionList)
        if val.startswith("/"):
            filtered = [(cmd, desc) for cmd, desc in self.SLASH_COMMANDS if cmd.startswith(val.lower())]
            opt_list.clear_options()
            if filtered:
                for cmd, desc in filtered:
                    opt_list.add_option(f"{cmd} - {desc}")
                opt_list.highlighted = 0
                opt_list.display = True
            else:
                opt_list.display = False
        else:
            opt_list.display = False

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        event.input.value = ""
        if isinstance(event.input, PromptInput):
            event.input.record_history(query)

        if getattr(self, "_file_picker_active", False):
            self._open_selected_file(query)
            return

        if getattr(self, "_session_picker_active", False):
            await self._open_selected_session(query)
            return

        self.query_one("#command-list", OptionList).display = False

        if not query.startswith("/") and getattr(self, "_is_agent_running", False):
            chat = self.query_one("#chat-container", VerticalScroll)
            chat.mount(Static(Markdown("**System:**\nOperation running. Please type `/stop` first or wait until the current operation finishes."), classes="agent-bubble"))
            chat.scroll_end(animate=False)
            return

        if query == "/files":
            self._show_file_picker()
            if not self._file_picker_active:
                chat = self.query_one("#chat-container", VerticalScroll)
                chat.mount(Static(Markdown("**System:**\nNo files currently stored in workspace buffer."), classes="agent-bubble"))
                chat.scroll_end(animate=False)
            return

        if query == "/exit": self.exit()
        elif query == "/stop":
            self._is_agent_running = False
            self.workers.cancel_all()
            chat = self.query_one("#chat-container", VerticalScroll)
            for widget in self.query("ToolCallWidget"):
                if not widget._done:
                    widget.mark_stopped()
            for widget in self.query("ProcessingWidget"):
                widget.mark_stopped()
            for widget in self.query("ThinkingWidget"):
                widget.finish()
            chat.mount(Static(Markdown("**System:**\nStopped."), classes="agent-bubble"))
            chat.scroll_end(animate=False)
        elif query == "/new":
            self._is_agent_running = False
            self.workers.cancel_all()
            reset_session()
            self._pending_clarify = None
            self._clarify_done = False
            self._active_run_dir = None
            self._conv_fetched = None
            self._conv_run_state = None

            global _current_session_id, _session_events, _current_call_by_source, _current_text_by_source
            _current_session_id = str(uuid.uuid4())
            _session_events.clear()
            _current_call_by_source.clear()
            _current_text_by_source.clear()

            chat = self.query_one("#chat-container", VerticalScroll)
            await chat.remove_children()
            chat.mount(self._banner_widget())
            chat.scroll_end(animate=False)
        elif query == "/toggle_thinking":
            config.cfg["settings"]["enable_thinking"] = not config.cfg["settings"]["enable_thinking"]
            config.save_config()
            state = "ON" if config.cfg["settings"]["enable_thinking"] else "OFF"
            chat = self.query_one("#chat-container", VerticalScroll)
            chat.mount(Static(Markdown(f"**System:**\nThinking capability is now **{state}**"), classes="agent-bubble"))
            chat.scroll_end(animate=False)
        elif query == "/toggle_persistence":
            config.cfg["settings"]["enable_session_persistence"] = not config.cfg["settings"].get("enable_session_persistence", False)
            config.save_config()
            state = "ON" if config.cfg["settings"]["enable_session_persistence"] else "OFF"
            chat = self.query_one("#chat-container", VerticalScroll)
            msg = f"**System:**\nSession persistence is now **{state}**."
            if config.cfg["settings"]["enable_session_persistence"]:
                log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
                log_file = log_dir / f"session_{_current_session_id}.json"
                msg += f"\nSaving to: `{log_file}`"
                _write_log()
            chat.mount(Static(Markdown(msg), classes="agent-bubble"))
            chat.scroll_end(animate=False)
        elif query == "/sessions":
            chat = self.query_one("#chat-container", VerticalScroll)
            log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
            if not log_dir.exists():
                chat.mount(Static(Markdown("**System:**\nNo sessions found."), classes="agent-bubble"))
            else:
                files = sorted(log_dir.glob("session_*.json"), key=os.path.getmtime, reverse=True)
                if not files:
                    chat.mount(Static(Markdown("**System:**\nNo sessions found."), classes="agent-bubble"))
                else:
                    lines = ["**Saved Sessions:**\n"]
                    for f in files[:10]:
                        try:
                            with open(f, "r") as fs:
                                j = json.load(fs)
                                ts = j.get("timestamp", "Unknown")
                                sid = j.get("session_id", f.stem.replace('session_', ''))
                                lines.append(f"- **ID:** `{sid}` (Date: {ts})")
                        except Exception:
                            lines.append(f"- Invalid session file: `{f.name}`")
                    chat.mount(Static(Markdown("\n".join(lines)), classes="agent-bubble"))
            chat.scroll_end(animate=False)
        elif query == "/resume":
            self._show_session_picker()
        elif query == "/config":
            chat = self.query_one("#chat-container", VerticalScroll)
            config_path = getattr(config, "_CONFIG_PATH", "Unknown")
            lines = [f"**System Configuration (Loaded from: `{config_path}`)**\n"]
            is_auto_approved = getattr(config, 'AUTO_APPROVE', False)
            if is_auto_approved:
                lines.insert(0, "> [!WARNING]\n> **AUTO_APPROVE ENABLED**: All Interactive execution safeguards are bypassed!\n\n")
            for section, values in config.cfg.items():
                lines.append(f"### {section.replace('_', ' ').title()}")
                if isinstance(values, dict):
                    for k, v in values.items():
                        lines.append(f"- **{k}:** `{v}`")
                else:
                    lines.append(f"- `{values}`")
                lines.append("")
            chat.mount(Static(Markdown("\n".join(lines)), classes="agent-bubble"))
            chat.scroll_end(animate=False)
        elif query:
            log_prompt(query)
            if self._pending_clarify:
                original, self._pending_clarify = self._pending_clarify, None
                self.run_agent(
                    f"{original}\n\nUSER CLARIFICATIONS (answers to the intake questions above):\n{query}"
                )
            elif not self._clarify_done and config.cfg.get("settings", {}).get("clarify_before_research", True):
                self.clarify_query(query)
            else:
                self.run_agent(query)

    async def _load_session_by_id(self, sid: str):
        chat = self.query_one("#chat-container", VerticalScroll)
        log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
        log_file = log_dir / f"session_{sid}.json"

        if not log_file.exists():
            chat.mount(Static(Markdown(f"**System:**\nSession `{sid}` not found."), classes="agent-bubble"))
            chat.scroll_end(animate=False)
            return

        try:
            with open(log_file, "r") as f:
                data = json.load(f)

            global _session_events, _current_session_id, _current_call_by_source, _current_text_by_source
            ui_events = data.get("ui_events", [])
            state_dict = data.get("agent_session", None)

            self._is_agent_running = False
            self.workers.cancel_all()

            _session_events = ui_events
            _current_session_id = sid
            _current_call_by_source.clear()
            _current_text_by_source.clear()

            orchestrator_module.reset_session()
            if state_dict:
                orchestrator_module.create_local_agent(builder=self.builder, session_data=state_dict)
            else:
                orchestrator_module.create_local_agent(builder=self.builder)

            await self.reconstruct_ui_from_events(ui_events)

            chat.mount(Static(Markdown(f"**System:**\nSession `{sid}` restored successfully!"), classes="agent-bubble"))
            chat.scroll_end(animate=False)

        except Exception as e:
            chat.mount(Static(Markdown(f"**System:**\nFailed to restore session `{sid}`: {e}"), classes="agent-bubble"))
            chat.scroll_end(animate=False)

    async def reconstruct_ui_from_events(self, events: list):
        chat = self.query_one("#chat-container", VerticalScroll)
        await chat.remove_children()
        chat.mount(self._banner_widget())

        active_tools = {}
        for event in events:
            source = event.get("source", "Agent")
            is_subagent = source.startswith("SubAgent_")
            etype = event.get("type")
            data = event.get("data", {})

            depth = event.get("depth", 1 if is_subagent else 0)

            def apply_depth_style(widget):
                if depth > 0:
                    widget.styles.margin = (0, 2, 1, 2 + (4 * depth))
                    widget.styles.border_left = ("vkey", "purple" if depth > 0 else "blue")
                return widget

            if etype == "prompt" and source == "User":
                chat.mount(UserMessageWidget(data.get("text", "")))
            elif etype == "subagent_start":
                status_widget = Static(f"[blue]▶[/blue] [bold]{source}[/bold] executing...", classes="agent-bubble")
                chat.mount(apply_depth_style(status_widget))
            elif etype == "subagent_end":
                elapsed = data.get("elapsed", 0.0)
                status_widget = Static(f"[green]✅[/green] [bold]{source}[/bold] finished ({elapsed:.1f}s)", classes="agent-bubble")
                chat.mount(apply_depth_style(status_widget))
            elif etype == "text":
                msg = AgentMessageWidget(source)
                msg.append_text(data.get("text", ""))
                chat.mount(apply_depth_style(msg))
            elif etype == "reasoning":
                tw = ThinkingWidget()
                tw.append(data.get("text", ""))
                tw.finish()
                chat.mount(apply_depth_style(tw))
            elif etype == "function_call":
                cid = data.get("call_id")
                w = ToolCallWidget(data.get("name"), cid, is_subagent=is_subagent, agent_name=source)
                w.append_args(data.get("arguments", ""))
                active_tools[cid] = w
                chat.mount(apply_depth_style(w))
            elif etype == "function_result":
                cid = data.get("call_id")
                res = data.get("result", "")
                if cid and cid in active_tools:
                    active_tools[cid].set_result(str(res))

        self._safe_scroll_end(chat)

    def _safe_scroll_end(self, chat: VerticalScroll) -> None:
        """Scroll to the bottom only if the user is already near the bottom."""
        if chat.max_scroll_y - chat.scroll_y <= 3:
            chat.scroll_end(animate=False)

    async def handle_agent_update(self, update, state, chat, is_subagent=False, agent_name=None, is_done=False):
        import time
        # Calculate dynamic nesting depth based on active delegation level
        depth = delegation_depth_ctx.get()

        if is_done and is_subagent:
            widget = state.get(f"widget_{agent_name}")
            if widget:
                start_time = state.get(f"start_time_{agent_name}", time.time())
                elapsed = time.time() - start_time
                widget.update(f"[green]✅[/green] [bold]{agent_name}[/bold] finished ({elapsed:.1f}s)")
                log_stream_content(agent_name, "subagent_end", {"elapsed": elapsed}, depth=depth)
            return

        # --- Extract reasoning_content from raw chunk delta ---
        raw_reasoning = None
        chat_update = getattr(update, "raw_representation", None)
        raw_chunk = getattr(chat_update, "raw_representation", None)
        if raw_chunk and hasattr(raw_chunk, "choices"):
            for ch in raw_chunk.choices:
                delta = getattr(ch, "delta", None)
                if delta:
                    extras = getattr(delta, "model_extra", None) or {}
                    raw_reasoning = extras.get("reasoning_content")

        has_any_content = bool(update.contents) or bool(raw_reasoning)
        if not state.get("has_first_token", False) and has_any_content:
            state["has_first_token"] = True
            widget = state.get("processing_widget")
            if widget:
                widget.stop()
                state["processing_widget"] = None

        source_name = agent_name if agent_name else ("Sub-Agent" if is_subagent else "Agent")

        def apply_depth_style(widget):
            if depth > 0:
                widget.styles.margin = (0, 2, 1, 2 + (4 * depth))
                widget.styles.border_left = ("vkey", "purple" if depth > 0 else "blue")
            return widget

        # Check for first-time sub-agent invocation
        if is_subagent and agent_name:
            import time
            spawned = state.setdefault("spawned_subagents", set())
            if agent_name not in spawned:
                spawned.add(agent_name)
                state[f"start_time_{agent_name}"] = time.time()
                # Mount a simple status indicator and store a reference to it
                status_widget = Static(f"[blue]▶[/blue] [bold]{agent_name}[/bold] executing...", classes="agent-bubble")
                state[f"widget_{agent_name}"] = status_widget
                chat.mount(apply_depth_style(status_widget))
                self._safe_scroll_end(chat)
                log_stream_content(agent_name, "subagent_start", {}, depth=depth)


        if raw_reasoning:
            log_stream_content(source_name, "reasoning", {"text": raw_reasoning}, depth=depth)
            if state.get("thinking_widget") is None:
                tw = ThinkingWidget()
                state["thinking_widget"] = tw
                chat.mount(apply_depth_style(tw))
            state["thinking_widget"].append(raw_reasoning)
            self._safe_scroll_end(chat)

        for content in update.contents:
            if content.type == "text_reasoning":
                reasoning_text = content.text or ""
                log_stream_content(source_name, "reasoning", {"text": reasoning_text}, depth=depth)
                if not reasoning_text and content.protected_data:
                    try:
                        details = json.loads(content.protected_data)
                        if isinstance(details, list):
                            reasoning_text = "\n".join(
                                d.get("text", "") for d in details if isinstance(d, dict)
                            )
                    except Exception:
                        pass
                if reasoning_text:
                    if state.get("thinking_widget") is None:
                        tw = ThinkingWidget()
                        state["thinking_widget"] = tw
                        chat.mount(apply_depth_style(tw))
                    state["thinking_widget"].append(reasoning_text)
                    self._safe_scroll_end(chat)

            elif content.type == "text":
                if is_subagent:
                    # Suppress subagent text from pouring into the main chat console.
                    # It will be cleanly presented as the final Tool Result when the delegation tool returns.
                    continue

                if content.text:
                    log_stream_content(source_name, "text", {"text": content.text})

                if state.get("thinking_widget") is not None:
                    state["thinking_widget"].finish()
                    state["thinking_widget"] = None
                if state["current_msg"] is None:
                    state["current_msg"] = AgentMessageWidget(source_name)
                    chat.mount(apply_depth_style(state["current_msg"]))
                state["current_msg"].append_text(content.text)
                self._safe_scroll_end(chat)

            elif content.type == "function_call":
                call_id = getattr(content, "call_id", None)
                name = getattr(content, "name", None)
                arguments = getattr(content, "arguments", "") or ""
                log_stream_content(source_name, "function_call", {
                    "call_id": call_id, "name": name, "arguments": arguments
                }, depth=depth)

                state["current_msg"] = None
                if content.call_id:
                    state["current_call_id"] = content.call_id
                    if content.call_id not in state["calls"]:
                        widget = ToolCallWidget(name=content.name, call_id=content.call_id, is_subagent=is_subagent, agent_name=source_name)
                        state["calls"][content.call_id] = widget
                        chat.mount(apply_depth_style(widget))
                    else:
                        widget = state["calls"][content.call_id]

                    if content.arguments:
                        widget.append_args(content.arguments)
                else:
                    call_id = state["current_call_id"]
                    if call_id and call_id in state["calls"] and content.arguments:
                        state["calls"][call_id].append_args(content.arguments)

            elif content.type == "function_result":
                call_id = getattr(content, "call_id", None)
                result = getattr(content, "result", "")
                log_stream_content(source_name, "function_result", {"call_id": call_id, "result": str(result)}, depth=depth)

                state["current_msg"] = None
                target_widget = None
                if call_id and call_id in state["calls"]:
                    target_widget = state["calls"].pop(call_id)

                if not target_widget:
                    target_name = getattr(content, "name", None)
                    if target_name:
                        for cid, cw in list(state["calls"].items()):
                            if cw.tool_name == target_name and not cw._done:
                                target_widget = state["calls"].pop(cid)
                                break

                if target_widget:
                    target_widget.set_result(str(getattr(content, "result", getattr(content, "content", "Executed."))))

        self._safe_scroll_end(chat)

    @work(exclusive=True, group="clarify")
    async def clarify_query(self, query: str):
        """Pre-research intake: one tool-less model call that either says CLEAR or asks up to 3
        scoping questions before the run starts — a mis-scoped run costs its full 15-60 minutes,
        the intake call costs seconds. Any failure falls through to research immediately: the
        clarifier can never block a run (see _clarify_verdict)."""
        self._is_agent_running = True
        chat = self.query_one("#chat-container", VerticalScroll)
        chat.mount(UserMessageWidget(query))
        processing = ProcessingWidget("Intake")
        chat.mount(processing)
        self._safe_scroll_end(chat)
        text = ""
        try:
            from engine.orchestrator import _build_client, _get_default_options
            from prompts import CLARIFY_INSTRUCTIONS
            clarifier = _build_client().as_agent(
                name="Clarifier", instructions=CLARIFY_INSTRUCTIONS,
                default_options=_get_default_options(),
            )
            stream = clarifier.run(query, stream=True)
            async for update in stream:
                for c in update.contents:
                    if c.type == "text" and c.text:
                        text += c.text
        except Exception:
            text = ""
        finally:
            processing.remove()
            self._is_agent_running = False

        questions = _clarify_verdict(text)
        if questions is None:
            self.run_agent(query, mount_user=False)
        else:
            self._pending_clarify = query
            chat.mount(Static(Markdown(
                f"**Intake — before I start researching:**\n\n{questions}\n\n"
                f"*Reply with answers, or say `proceed` to research as-is. "
                f"Research starts with your next message.*"
            ), classes="agent-bubble"))
            self._safe_scroll_end(chat)
            log_stream_content("Intake", "text", {"text": questions})

    @work(exclusive=True)
    async def run_agent(self, query: str, mount_user: bool = True):
        self._is_agent_running = True
        self._clarify_done = True

        # Session directory isolation: when enabled, ALL workspace file operations for this run
        # are transparently mapped to a subfolder named from the query + timestamp (e.g.
        # 'grasshopper_optimization_algorithm_20260710_192335/'), not a bare unix timestamp — see
        # _slugify_run_dir_name. Toggle via config.yaml: settings.workspace.session_isolation: true
        session_token = None
        run_dir_name = None
        is_followup = self._active_run_dir is not None
        if config.cfg.get("settings", {}).get("workspace", {}).get("session_isolation", False):
            from tools.fs import session_dir_ctx
            run_dir_name = self._active_run_dir if is_followup else _slugify_run_dir_name(query)
            self._active_run_dir = run_dir_name
            session_token = session_dir_ctx.set(run_dir_name)

        # Initialize tool quotas from config (fresh pool per turn, including follow-ups)
        quota_token = tool_quotas_ctx.set(build_quota_pool())
        from utils.run_state import fetched_urls_ctx
        if is_followup and self._conv_fetched is not None:
            # Workers each get their own contextvars copy — carry the conversation's fetched-URL
            # list across turns via the instance, so follow-up answers citing turn-1 sources
            # still pass the grounding check.
            fetched_urls_ctx.set(self._conv_fetched)
        else:
            reset_fetched_urls()
        # The ctx list itself, NOT get_fetched_urls() — that helper returns a fresh [] when the
        # record is empty, which would silently break the shared-object carry across turns.
        self._conv_fetched = fetched_urls_ctx.get()
        run_state_token = None

        chat = self.query_one("#chat-container", VerticalScroll)
        if mount_user:
            chat.mount(UserMessageWidget(query))

        # Set up subagent callback context dict
        subagent_states = {}

        async def ui_callback(update, is_subagent=True, is_done=False, **kwargs):
            aname = kwargs.get("agent_name", "Sub-Agent")
            if aname not in subagent_states:
                subagent_states[aname] = {"calls": {}, "current_call_id": None, "current_msg": None}

            requests = kwargs.get("approval_requests", [])
            if requests:
                from agent_framework import Message, Content
                from tools import WORKSPACE_TOOLS
                responses = []
                for req in requests:
                    is_auto_approved = getattr(config, 'AUTO_APPROVE', False)
                    if not is_auto_approved:
                        widget = ApprovalWidget(req.function_call.name, agent_name=aname, arguments=getattr(req.function_call, "arguments", ""))
                        chat.mount(widget)
                        chat.scroll_end(animate=False)
                        await widget.event.wait()
                        is_approved = widget.approved
                    else:
                        is_approved = True

                    call_id = getattr(req.function_call, "id", None) if hasattr(req, "function_call") else None
                    target_widget = subagent_states[aname]["calls"].get(call_id)
                    if not target_widget:
                        for cw in subagent_states[aname]["calls"].values():
                            if hasattr(req, "function_call") and cw.tool_name == req.function_call.name and not cw._done:
                                target_widget = cw
                                break

                    if is_approved:
                        args_dict = req.function_call.parse_arguments() or {}
                        tool_func = next((t for t in WORKSPACE_TOOLS if t.name == req.function_call.name), None)
                        try:
                            if tool_func and hasattr(tool_func, "func"):
                                result_str = str(tool_func.func(**args_dict))
                            else:
                                result_str = "Executed natively."
                        except Exception as e:
                            result_str = f"Error: {e}"

                        if target_widget:
                            target_widget.set_result(result_str)
                            log_stream_content(aname, "function_result", {
                                "call_id": getattr(req.function_call, "call_id", getattr(req.function_call, "id", None)),
                                "result": result_str
                            })

                        responses.append(Message("assistant", [req.function_call]))
                        responses.append(Message("tool", [Content.from_function_result(
                            call_id=getattr(req.function_call, "call_id", getattr(req.function_call, "id", None)),
                            result=result_str
                        )]))
                    else:
                        if target_widget:
                            target_widget.set_result("Denied by user.")
                            log_stream_content(aname, "function_result", {
                                "call_id": getattr(req.function_call, "call_id", getattr(req.function_call, "id", None)),
                                "result": "Denied by user."
                            })
                        responses.append(Message("assistant", [req.function_call]))
                        responses.append(Message("user", [req.to_function_approval_response(False)]))
                return responses

            if update or is_done:
                await self.handle_agent_update(update, subagent_states.setdefault(aname, {"calls": {}, "current_call_id": None, "current_msg": None}), chat, is_subagent=is_subagent, agent_name=aname, is_done=is_done)

        try:
            # Create agent (re-reads config) and get session (None if conversational memory disabled)
            agent, session = create_local_agent(builder=self.builder, subagent_callback=ui_callback)
            current_input = query
            has_requests = True
            state = {"calls": {}, "current_call_id": None, "current_msg": None}
            if is_followup and self._conv_run_state is not None:
                # Same conversation, same _run_state.json: one continuous forensic timeline.
                # attempt resets so each turn gets its own nudge budget; the original query stays
                # (it drives the exclusion gate — a follow-up rarely restates the exclusions).
                run_state = self._conv_run_state
                run_state.attempt = 0
                # no_urls_count is a standalone per-turn escalation counter (unlike
                # completion_check_attempts, which is meant to keep growing as one continuous
                # forensic timeline across turns) — without this reset, a follow-up turn's first
                # zero-citation nudge inherits the PRIOR turn's escalation count and immediately
                # fires the "Nth time in a row" language, even though this turn never failed
                # before. Second full audit, 2026-07-12, item 7.
                run_state.data.pop("no_urls_count", None)
                run_state.data.setdefault("followup_queries", []).append(query)
            else:
                run_state = RunState(_current_run_dir(run_dir_name))
                run_state.set_query(query)
                self._conv_run_state = run_state
            run_state_token = run_state_ctx.set(run_state)
            # A follow-up in a conversation whose report already exists is Q&A over the gathered
            # research, not a new research run — the artifact/grounding contract was already
            # enforced when the report was produced. ponytail: follow-up answers themselves are
            # unverified chat; per-turn grounding of follow-ups if that ever proves insufficient.
            skip_completion_check = is_followup and (
                config.cfg.get("settings", {}).get("workspace", {}).get("required_artifact", "final_report.md")
                in get_workspace_files()
            )

            while has_requests:
                has_requests = False
                user_input_requests = []

                stream = agent.run(current_input, session=session, stream=True)
                state["current_msg"] = None
                state["has_first_token"] = False
                state["processing_widget"] = ProcessingWidget("Agent")
                chat.mount(state["processing_widget"])
                self._safe_scroll_end(chat)

                try:
                    async for update in stream:
                        await self.handle_agent_update(update, state, chat, is_subagent=False)

                        if hasattr(update, "user_input_requests") and update.user_input_requests:
                            user_input_requests.extend(update.user_input_requests)

                    # -------------------------------------------------------------
                    # [!CAUTION] AGENT-FRAMEWORK SYNCHRONIZATION BUGFIX
                    # -------------------------------------------------------------
                    # The agent framework's ResponseStream only populates `session.state`
                    # via its `after_run` hooks AFTER the async generator exhausts entirely.
                    # Since _write_log is constantly called mid-stream by log_stream_content,
                    # the final file written during standard generation would often contain
                    # `{"state": {"in_memory": {}}}` because the stream hadn't reached its end yet.
                    # We definitively evaluate `_write_log()` here once the stream guarantees finalization.
                    _write_log()

                except Exception as e:
                    p_widget = state.get("processing_widget")
                    if p_widget:
                        p_widget.mark_error(str(e))
                        state["processing_widget"] = None
                    else:
                        chat.mount(Static(f"[red]Error: {str(e)}[/red]", classes="agent-bubble"))
                    chat.scroll_end(animate=False)

                if user_input_requests:
                    has_requests = True
                    # current_input, not query: an approval request arriving after a completion-
                    # check nudge (run_completion_check reassigns current_input to include the
                    # injected retry message) must keep that nudge in the rebuilt input list, or
                    # the retry silently reverts to the bare original prompt — the exact bug
                    # run_cli's copy of this same loop was already fixed for (see its "current_input,
                    # not prompt" comment below). Second full audit, 2026-07-12, item 1.
                    new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)

                    for req in user_input_requests:
                        # Mount the interactive widget conditionally
                        is_auto_approved = getattr(config, 'AUTO_APPROVE', False)
                        if not is_auto_approved:
                            widget = ApprovalWidget(req.function_call.name, agent_name="Planner", arguments=getattr(req.function_call, "arguments", ""))
                            chat.mount(widget)
                            chat.scroll_end(animate=False)

                            # Pause loop to wait for physical user interaction event loop
                            await widget.event.wait()
                            is_approved = widget.approved
                        else:
                            is_approved = True

                        call_id = getattr(req.function_call, "id", None) if hasattr(req, "function_call") else None
                        target_widget = state["calls"].get(call_id)
                        if not target_widget:
                            for cw in state["calls"].values():
                                if hasattr(req, "function_call") and cw.tool_name == req.function_call.name and not cw._done:
                                    target_widget = cw
                                    break

                        if is_approved:
                            args_dict = req.function_call.parse_arguments() or {}
                            from tools import WORKSPACE_TOOLS
                            tool_func = next((t for t in WORKSPACE_TOOLS if t.name == req.function_call.name), None)
                            try:
                                if tool_func and hasattr(tool_func, "func"):
                                    result_str = str(tool_func.func(**args_dict))
                                else:
                                    result_str = "Executed natively."
                            except Exception as e:
                                result_str = f"Error: {e}"

                            if target_widget:
                                target_widget.set_result(result_str)
                                log_stream_content("Agent", "function_result", {
                                    "call_id": getattr(req.function_call, "call_id", getattr(req.function_call, "id", None)),
                                    "result": result_str
                                })

                            new_inputs.append(Message("assistant", [req.function_call]))
                            new_inputs.append(Message("tool", [Content.from_function_result(
                                call_id=getattr(req.function_call, "call_id", getattr(req.function_call, "id", None)),
                                result=result_str
                            )]))
                        else:
                            if target_widget:
                                target_widget.set_result("Denied by user.")
                                log_stream_content("Agent", "function_result", {
                                    "call_id": getattr(req.function_call, "call_id", getattr(req.function_call, "id", None)),
                                    "result": "Denied by user."
                                })
                            new_inputs.append(Message("assistant", [req.function_call]))
                            new_inputs.append(Message("user", [req.to_function_approval_response(False)]))

                    # Push back upstream and flush state
                    current_input = new_inputs

                if not has_requests and not skip_completion_check:
                    def _tui_notify(msg: str):
                        chat.mount(Static(Markdown(msg), classes="agent-bubble"))
                        chat.scroll_end(animate=False)
                        log_stream_content("Agent", "text", {"text": msg})

                    turn_msg = state.get("current_msg")
                    should_continue, current_input = await run_completion_check(
                        query=query, current_input=current_input, run_state=run_state, notify=_tui_notify,
                        last_assistant_text=turn_msg.text if turn_msg else "",
                    )
                    if should_continue:
                        has_requests = True

            run_state.save()
        finally:
            tool_quotas_ctx.reset(quota_token)
            if run_state_token is not None:
                run_state_ctx.reset(run_state_token)
            if session_token is not None:
                from tools.fs import session_dir_ctx
                session_dir_ctx.reset(session_token)
            self._is_agent_running = False

    def _render_cmd_list(self) -> None:
        panel = self.query_one("#command-list", OptionList)
        if not self._filtered_cmds:
            panel.display = False
            return
        panel.clear_options()
        for i, (cmd, desc) in enumerate(self._filtered_cmds):
            panel.add_option(f"{cmd} - {desc}")
        panel.highlighted = 0
        panel.display = True

    def _show_file_picker(self) -> None:
        files = get_workspace_files()
        if not files:
            self._file_picker_files = []
            self._file_picker_active = False
            return
        self._file_picker_files = files
        self._file_picker_active = True
        self._filtered_cmds = [
            (f, f"{len((get_workspace_file_content(f) or '').encode('utf-8'))} bytes")
            for f in files
        ]
        self._render_cmd_list()

    def _show_session_picker(self) -> None:
        log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
        if not log_dir.exists():
            chat = self.query_one("#chat-container", VerticalScroll)
            chat.mount(Static(Markdown("**System:**\nNo sessions found."), classes="agent-bubble"))
            chat.scroll_end(animate=False)
            return

        files = sorted(log_dir.glob("session_*.json"), key=os.path.getmtime, reverse=True)
        if not files:
            chat = self.query_one("#chat-container", VerticalScroll)
            chat.mount(Static(Markdown("**System:**\nNo sessions found."), classes="agent-bubble"))
            chat.scroll_end(animate=False)
            return

        self._session_picker_active = True
        self._filtered_cmds = []

        for f in files[:15]:
            try:
                with open(f, "r") as fs:
                    j = json.load(fs)
                    ts = j.get("timestamp", "Unknown")
                    sid = j.get("session_id", f.stem.replace("session_", ""))
                    self._filtered_cmds.append((sid, f"Date: {ts}"))
            except Exception:
                pass

        self._render_cmd_list()

    def _display_file(self, filename: str, collapsed_by_default: bool = False) -> None:
        content = get_workspace_file_content(filename)
        if content is None: return

        chat_container = self.query_one("#chat-container", VerticalScroll)
        try:
            file_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
            copy_btn = Button("📋", id=f"copy-btn-{id(file_log)}", classes="title-copy-btn")
            copy_btn._file_content = content
            inner = Vertical(copy_btn, file_log, classes="file-viewer-inner")
            viewer = Collapsible(inner, title=f"\N{OPEN FILE FOLDER} {filename}", classes="file-viewer-collapsible")
            wrapper = Vertical(viewer, classes="tool-call file-viewer-wrapper")
            chat_container.mount(wrapper)
            viewer.collapsed = collapsed_by_default
            file_log.write(content)
        except Exception as e:
            chat_container.mount(Static(Markdown(f"**System:**\nError reading {filename}: {e}"), classes="agent-bubble"))
        chat_container.scroll_end(animate=False)

    @on(Button.Pressed, ".title-copy-btn")
    def on_copy_button(self, event: Button.Pressed) -> None:
        if hasattr(event.button, "_file_content"):
            self.app.copy_to_clipboard(event.button._file_content)
            btn = event.button
            btn.label = "✅"
            def reset():
                btn.label = "📋"
            self.set_timer(2.0, reset)

    def _open_selected_file(self, filename: str) -> None:
        if not self._file_picker_active:
            return
        self._display_file(filename)
        self._file_picker_active = False
        self._filtered_cmds = []
        self.query_one("#command-list", OptionList).display = False

    async def _open_selected_session(self, session_id: str) -> None:
        if not getattr(self, "_session_picker_active", False):
            return
        self._session_picker_active = False
        self._filtered_cmds = []
        self.query_one("#command-list", OptionList).display = False
        await self._load_session_by_id(session_id)


# =================================================================================
# SHARED COMPLETION-CHECK LOGIC (used by both the interactive TUI's run_agent and
# headless run_cli)
#
# The old project duplicated this ~150-line block almost verbatim between the two
# entry points — every fix (including the ones documented in the old project's own
# SESSION_STATUS.md) had to be hand-applied twice, which is exactly the kind of
# drift risk that causes a fix to silently only land in one path. Factoring it out
# means the structural fixes below (real grounding check, per-attempt quota top-up,
# artifact quarantine, structured run-state) automatically apply identically to
# both the TUI and headless paths.
# =================================================================================

def _slugify_run_dir_name(query: str) -> str:
    """Short, human-readable run folder name instead of a bare unix timestamp — e.g.
    'grasshopper_optimization_algorithm_used_on_20260710_192335' instead of 'run_1783729333',
    which gave no hint what a given run's output folder was actually about. Purely deterministic
    from the query text already available at run start (no LLM call needed)."""
    slug = re.sub(r'[^a-z0-9]+', '_', (query or "").lower()).strip('_')
    slug = re.sub(r'_+', '_', slug)[:50].strip('_') or "query"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{slug}_{timestamp}"


def _current_run_dir(run_dir_name: str | None) -> str:
    base = config.cfg.get("settings", {}).get("workspace", {}).get("dir", ".")
    if run_dir_name:
        return os.path.join(base, run_dir_name)
    return base


def load_resume_state(resume_run: str) -> tuple[str, dict]:
    """Resolve --resume-run to (run_dir_name, prior _run_state.json contents). Accepts either a
    bare run-folder name or a path to it; the folder must live under the configured workspace dir
    and contain _run_state.json — guaranteed for any run since 497906c, which writes it at run
    start and on every fetch, precisely so an interrupted run stays continuable."""
    run_dir_name = os.path.basename(os.path.normpath(resume_run))
    state_path = os.path.join(_current_run_dir(run_dir_name), "_run_state.json")
    with open(state_path, "r", encoding="utf-8") as f:
        return run_dir_name, json.load(f)


def build_resume_input(query: str, prior_state: dict) -> str:
    """Planner input for a resumed run: the original task plus everything the interrupted run
    already established. Injected engine-side because the Planner deliberately has no
    read_workspace_file tool and a fresh run starts with an empty context — without this it can
    see that findings.md exists but never what's in it. Requires session_dir_ctx to already point
    at the resumed run's folder."""
    from tools.fs import get_workspace_file_content
    fetched = prior_state.get("fetched_urls") or []
    url_lines = "\n".join(f"- {u.get('url')} (saved as {u.get('filename')})" for u in fetched[:40])
    todos_txt = (get_workspace_file_content("_todos.md") or "").strip()
    findings_txt = (get_workspace_file_content("findings.md") or "").strip()
    parts = [
        "RESUMED RUN: a previous research run on this exact task was interrupted partway "
        "through. Its workspace is intact — do NOT restart from scratch and do NOT re-fetch "
        "sources listed below. Continue from where it stopped: delegate new research only for "
        "the gaps, then complete findings.md and the final report as normal.",
        f"ORIGINAL TASK:\n{query}",
    ]
    if todos_txt:
        parts.append(f"PLAN LEFT BY THE PREVIOUS RUN (_todos.md):\n{todos_txt[:2000]}")
    if url_lines:
        parts.append(
            f"SOURCES ALREADY FETCHED ({len(fetched)} total — they count as fetched for "
            f"grounding purposes, cite them freely):\n{url_lines}"
        )
    if findings_txt:
        parts.append(f"EXISTING findings.md (verbatim, possibly partial):\n{findings_txt[:8000]}")
    return "\n\n".join(parts)


# --depth presets: one flag instead of hand-editing quotas/search_mode per run. "standard" is a
# deliberate no-op (whatever config.yaml says); quick/deep only touch the three quotas that govern
# research volume plus search depth and the retry budget.
_DEPTH_PRESETS = {
    "quick": {"quotas": {"delegate_tasks": 8, "web_search": 8, "fetch_url_to_workspace": 8},
              "search_mode": "light", "max_completion_check_attempts": 2},
    "standard": {},
    "deep": {"quotas": {"delegate_tasks": 25, "web_search": 30, "fetch_url_to_workspace": 30},
             "search_mode": "heavy", "max_completion_check_attempts": 4},
}


def apply_depth_preset(cfg: dict, depth: str) -> None:
    preset = _DEPTH_PRESETS.get(depth) or {}
    quotas = cfg.setdefault("settings", {}).setdefault("quotas", {})
    for k, v in (preset.get("quotas") or {}).items():
        if isinstance(quotas.get(k), dict):
            quotas[k]["limit"] = v
        else:
            quotas[k] = v
    for k in ("search_mode", "max_completion_check_attempts"):
        if k in preset:
            cfg["settings"][k] = preset[k]


def _clarify_verdict(text: str) -> str | None:
    """None => proceed straight to research; a string => show it as clarifying questions first.
    An empty, CLEAR-prefixed, or rambling reply (>600 chars — the intake prompt asked for at most
    3 short questions) all mean 'proceed': the clarifier must never be able to block a run."""
    v = (text or "").strip()
    if not v or v.upper().startswith("CLEAR") or len(v) > 600:
        return None
    return v


# The completion-check engine (verdict checks, quarantine/restore/salvage) lives in
# engine/completion.py as a data-driven check list — see its header for why it left this file.
# Re-imported here because this module's callers and test_structural_checks.py address it as
# engine.tui, and _find_last_substantial_text below is its lazy tui-side dependency.
from engine.completion import (  # noqa: F401 — re-exports
    run_completion_check,
    _restore_quarantined_draft,
)


def _find_last_substantial_text(min_len: int = 200) -> str:
    """Scans the full session event history backward for the most recent substantial narrated
    text block from the main agent (not a sub-agent), stripping any System nudge messages that
    get coalesced into the same event by log_stream_content's per-source text merging.

    Fixes a real, confirmed bug: passing only the immediately-preceding turn's text to salvage
    loses a good narrated report from an earlier turn when a later retry's turn produces no text
    at all. Traced end-to-end against a live session log (session_ffe5dbc7-...json): the model
    narrated a complete, well-formed ~1000-char report on the second-to-last attempt, but the
    final attempt's turn was empty, so the old single-turn-lookback salvage saw "" and gave up —
    discarding a report that was sitting right there in the event history the whole time."""
    for event in reversed(_session_events):
        if event.get("source") != "Agent" or event.get("type") != "text":
            continue
        if event.get("depth", 0):
            continue
        text = event.get("data", {}).get("text", "")
        text = re.sub(r'System \(\d+/\d+\):.*', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'System \(final\):.*', '', text, flags=re.DOTALL).strip()
        if len(text) >= min_len:
            return text
    return ""


async def run_cli(builder, prompt: str = None, prompt_file: str = None, session_id: str = None,
                  resume_run: str = None, seed_urls: list = None):
    """Run the agent in headless mode, streaming results to stdout."""
    quota_token = tool_quotas_ctx.set(build_quota_pool())
    reset_fetched_urls()
    run_state_token = None

    session_token = None
    run_dir_name = None

    async def cli_subagent_callback(update, is_subagent=True, is_done=False, **kwargs):
        agent_name = kwargs.get("agent_name") or getattr(update, "author_name", None) or "Sub-Agent"

        requests = kwargs.get("approval_requests", [])
        if requests:
            from agent_framework import Message
            responses = []
            for req in requests:
                is_approved = getattr(config, 'AUTO_APPROVE', False)
                if is_approved:
                    sys.stdout.write(f"\n\033[93m[{agent_name}] Auto-approving {req.function_call.name}...\033[0m\n")
                else:
                    sys.stdout.write(f"\n\033[91m[{agent_name}] Denied {req.function_call.name} (Auto-approve disabled).\033[0m\n")
                responses.append(Message("user", [req.to_function_approval_response(is_approved)]))
            return responses

        if is_done:
            sys.stdout.write(f"\n\033[92m[{agent_name}] Finished.\033[0m\n")
            return

        if update is None:
            return

        for content in update.contents:
            if content.type == "text" and content.text:
                log_stream_content(agent_name, "text", {"text": content.text})
            elif content.type == "function_call":
                call_id = getattr(content, "call_id", None)
                name = getattr(content, "name", None)
                arguments = getattr(content, "arguments", "") or ""
                log_stream_content(agent_name, "function_call", {
                    "call_id": call_id, "name": name, "arguments": arguments
                })
                if call_id:
                    sys.stdout.write(f"\n\033[93m[{agent_name}] Calling {name}...\033[0m\n")
            elif content.type == "function_result":
                call_id = getattr(content, "call_id", None)
                result = getattr(content, "result", "")
                log_stream_content(agent_name, "function_result", {
                    "call_id": call_id, "result": str(result)
                })

    session_data = None
    if session_id:
        log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
        log_file = log_dir / f"session_{session_id}.json"

        if not log_file.exists():
            sys.stdout.write(f"\n\033[91mError: Session '{session_id}' not found.\033[0m\n")
            return

        try:
            with open(log_file, "r") as f:
                data = json.load(f)

            global _session_events, _current_session_id, _current_call_by_source, _current_text_by_source
            _session_events = data.get("ui_events", [])
            _current_session_id = session_id
            _current_call_by_source.clear()
            _current_text_by_source.clear()

            orchestrator_module.reset_session()
            session_data = data.get("agent_session", None)

            config.cfg["settings"]["enable_session_persistence"] = True

        except Exception as e:
            sys.stdout.write(f"\n\033[91mError loading session '{session_id}': {e}\033[0m\n")
            return

    if prompt_file:
        log_prompt(f"Started headless mode using prompt file: {prompt_file}")
    elif prompt:
        log_prompt(prompt)

    agent, session = create_local_agent(builder=builder, subagent_callback=cli_subagent_callback, session_data=session_data)

    if prompt_file:
        try:
            with open(prompt_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                prompt = "\n\n".join([f"{msg.get('role', 'user').upper()}:\n{msg.get('content', '')}" for msg in data])
            else:
                prompt = json.dumps(data)
        except Exception as e:
            sys.stdout.write(f"\n\033[91mError reading prompt file: {e}\033[0m\n")
            return

    # --resume-run: reattach to an interrupted run's workspace instead of minting a new one.
    # A run killed at minute 18 of 20 (throttle, 429, crash, power loss) previously lost every
    # fetched source even though _run_state.json + sources/ held everything needed to continue.
    prior_state = None
    if resume_run:
        try:
            run_dir_name, prior_state = load_resume_state(resume_run)
        except Exception as e:
            sys.stdout.write(f"\n\033[91mError: --resume-run '{resume_run}': cannot load its _run_state.json ({e}).\033[0m\n")
            sys.exit(1)
        if not prompt:
            prompt = prior_state.get("query")
        if not prompt:
            sys.stdout.write("\n\033[91mError: --resume-run: the previous run recorded no query — pass --prompt explicitly.\033[0m\n")
            sys.exit(1)
        from tools.fs import session_dir_ctx
        session_token = session_dir_ctx.set(run_dir_name)
        # The grounding check's source of truth: URLs the interrupted run actually fetched still
        # count as fetched, otherwise every prior citation would be flagged as fabricated.
        from utils.run_state import fetched_urls_ctx
        fetched_urls_ctx.set(list(prior_state.get("fetched_urls") or []))
    # Deferred until here (not right at function start) so a --prompt-file run's folder name is
    # slugified from the actual resolved prompt text, not generated before it's known.
    elif config.cfg.get("settings", {}).get("workspace", {}).get("session_isolation", False):
        from tools.fs import session_dir_ctx
        run_dir_name = _slugify_run_dir_name(prompt or prompt_file or "query")
        session_token = session_dir_ctx.set(run_dir_name)

    # Print Headless Configuration Banner
    config_path = getattr(config, "_CONFIG_PATH", "Unknown")
    workspace_type = config.cfg.get("settings", {}).get("workspace", {}).get("type", "memory")
    workspace_dir = config.cfg.get("settings", {}).get("workspace", {}).get("dir", ".")
    workspace_disp = f"Disk ({workspace_dir})" if workspace_type == "disk" else "In-Memory"

    endpoint = config.cfg.get("api", {}).get("openai_base_url", "Unknown")
    model = config.cfg.get("api", {}).get("openai_model", "Unknown")

    thinking = "ON" if config.cfg.get("settings", {}).get("enable_thinking", False) else "OFF"
    thinking_color = "32" if thinking == "ON" else "31"

    memory = "ON" if config.cfg.get("settings", {}).get("enable_conversational_memory", False) else "OFF"
    memory_color = "32" if memory == "ON" else "31"

    persistence_val = "ON" if config.cfg.get("settings", {}).get("enable_session_persistence", False) else "OFF"
    persistence_color = "32" if persistence_val == "ON" else "31"

    sid = "N/A (Memory disabled)" if not session else _current_session_id

    auto_approve_warning = "\n  \033[5;31m⚠️ AUTO-APPROVE OVERRIDE ACTIVE - ALL INTERACTIVE SAFEGUARDS BYPASSED\033[0m" if getattr(config, 'AUTO_APPROVE', False) else ""

    sys.stdout.write(
        f"\n\033[1;32m{config.APP_TITLE} (Headless Mode)\033[0m\n"
        f"  \033[2mConfig Loaded:\033[0m \033[90m{config_path}\033[0m  \033[2mWorkspace:\033[0m \033[33m{workspace_disp}\033[0m\n"
        f"  \033[2mEndpoint:\033[0m \033[36m{endpoint}\033[0m  \033[2mModel:\033[0m \033[36m{model}\033[0m  \033[2mThinking:\033[0m \033[{thinking_color}m{thinking}\033[0m  \033[2mConv Memory:\033[0m \033[{memory_color}m{memory}\033[0m\n"
        f"  \033[2mSession ID:\033[0m \033[90m{sid}\033[0m  \033[2mPersistence:\033[0m \033[{persistence_color}m{persistence_val}\033[0m"
        f"{auto_approve_warning}\n"
    )

    if prior_state:
        sys.stdout.write(
            f"\033[93m[System] RESUMING run '{run_dir_name}' — "
            f"{len(prior_state.get('fetched_urls') or [])} sources already fetched, "
            f"{len(prior_state.get('findings') or [])} findings on record.\033[0m\n"
        )
    sys.stdout.write(f"\n\033[1mStarting task:\033[0m {prompt[:100]}...\n\n")
    start_time = datetime.now()

    # Wall-clock budget (headless only — a TUI user can /stop; an unattended run can't). 0 = off.
    # On expiry the run is not hard-killed: the current turn is cut, then the completion check is
    # forced straight to its final-verdict path, so salvage/labeling still runs and the run ends
    # with an explicit verdict instead of a silent overrun (a qwen run once ran 64 minutes).
    max_run_minutes = config.cfg.get("settings", {}).get("max_run_minutes", 0) or 0
    budget_deadline = (time.monotonic() + max_run_minutes * 60) if max_run_minutes else None

    run_state = None
    try:
        from agent_framework import Message
        current_input = build_resume_input(prompt, prior_state) if prior_state else prompt
        has_requests = True
        run_state = RunState(_current_run_dir(run_dir_name))
        if prior_state:
            # Carry the interrupted run's record forward — same _run_state.json, one continuous
            # timeline. attempt stays 0: resume exists to finish the run, so it gets a fresh
            # completion-check budget (and a fresh quota pool from the top of this function).
            # ponytail: fresh quotas on resume — quotas exist to stop model loops, not to meter
            # cross-run budgets; per-run carryover accounting if that ever proves too generous.
            # "query" is included even though set_query() follows immediately: a crash between
            # this merge and set_query must never save a query-less state file over the original
            # (exactly that happened on this feature's first live smoke test).
            for key in ("query", "findings", "fetched_urls", "completion_check_attempts",
                        "search_health", "started_at", "plan"):
                if key in prior_state:
                    run_state.data[key] = prior_state[key]
            run_state.data["resumed_at"] = time.time()
        run_state.set_query(prompt)
        run_state_token = run_state_ctx.set(run_state)
        # Written immediately (not only at the first completion check / clean run end) so a crash
        # or power loss mid-research still leaves a forensic _run_state.json behind — confirmed
        # live 2026-07-11: a NIM 429 killed a run 10 minutes in and left 15 fetched files with no
        # state record at all, making the run unscoreable.
        run_state.save()

        # Pre-run search health probe (headless only — an unattended run can't notice a sick
        # search layer until 20 wasted minutes later; a TUI user sees failures live). Aborting
        # here costs ~5 seconds and leaves an explicit environmental verdict instead of a
        # doomed run that looks like model failure.
        from tools.web import probe_search_health
        from utils.run_state import record_search_health
        probe_err = await asyncio.to_thread(probe_search_health)
        if probe_err:
            record_search_health(ok=False)
            sys.stdout.write(
                f"\n\033[91m[System] ENVIRONMENT UNHEALTHY — aborting before research starts: "
                f"the search layer failed a trivial probe ({probe_err}). This is not a model "
                f"problem; re-run when search egress recovers.\033[0m\n"
            )
            _write_log()
            sys.exit(1)

        # --seed-url: the user often already has the 2-3 links (or the regulation PDF) the
        # research should start from. Fetched through the normal pipeline, so they land in
        # sources/ with Source-URL provenance and count as fetched for the grounding check —
        # but refunded from the quota pool, since they're user-provided, not agent spend.
        if seed_urls:
            from tools.web import fetch_url_to_workspace, _slugify_for_filename
            from tools.core import refund_quota
            seeded = []
            for u in seed_urls:
                result = await fetch_url_to_workspace.func(url=u, filename=_slugify_for_filename(u, "seed"))
                refund_quota("fetch_url_to_workspace")
                ok = not str(result).lstrip().startswith("Failed")
                sys.stdout.write(f"\033[93m[System] Seed {'fetched' if ok else 'FAILED'}: {u}\033[0m\n")
                if ok:
                    seeded.append(u)
            if seeded:
                current_input += (
                    "\n\nSEED SOURCES (provided by the user, already fetched into the workspace "
                    "under sources/ — have your Analyzers read these before searching the open "
                    "web, and cite them like any fetched source):\n"
                    + "\n".join(f"- {u}" for u in seeded)
                )

        malformed_retries = 0
        # Context-budget guard for the Planner stream (see orchestrator.get_context_budget) —
        # headless only, same policy as max_run_minutes. Counts streamed chars across the whole
        # run (with conversational memory the session accumulates across turns).
        from engine.orchestrator import get_context_budget, stream_content_chars
        context_budget = get_context_budget()
        run_stream_chars = 0
        budget_nudged = False

        while has_requests:
            has_requests = False
            user_input_requests = []
            turn_text = ""

            try:
                stream = agent.run(current_input, session=session, stream=True)
                # Manually driven __anext__ + asyncio.wait_for, NOT `async for update in stream`
                # (found live, 2026-07-12): the plain async-for version only checks budget_deadline
                # once it actually RECEIVES an update — if the underlying stream goes a very long
                # time between updates (confirmed with Tongyi-DeepResearch: one single massive
                # <think> block round-tripped 1h6min+ past a configured max_run_minutes: 60 with
                # the GPU still actively generating and zero cutoff), the deadline check never gets
                # a chance to run at all. Racing each __anext__() against the remaining budget via
                # asyncio.wait_for fires the cutoff on a real wall-clock timer regardless of how
                # long the stream goes quiet, and needs no assumption about how finely the
                # underlying model/framework chunks its output.
                stream_iter = stream.__aiter__()
                while True:
                    if budget_deadline:
                        remaining = budget_deadline - time.monotonic()
                        if remaining <= 0:
                            sys.stdout.write(
                                f"\n\033[91m[System] max_run_minutes ({max_run_minutes}) exceeded — "
                                f"cutting the current turn short.\033[0m\n"
                            )
                            break
                    else:
                        remaining = None
                    try:
                        update = await asyncio.wait_for(stream_iter.__anext__(), timeout=remaining)
                    except asyncio.TimeoutError:
                        sys.stdout.write(
                            f"\n\033[91m[System] max_run_minutes ({max_run_minutes}) exceeded — "
                            f"cutting the current turn short (stream produced no update before the "
                            f"deadline).\033[0m\n"
                        )
                        break
                    except StopAsyncIteration:
                        break
                    run_stream_chars += stream_content_chars(update)
                    if context_budget and run_stream_chars > context_budget:
                        sys.stdout.write(
                            f"\n\033[91m[System] context_budget_chars ({context_budget}) exceeded — "
                            f"cutting the current turn short.\033[0m\n"
                        )
                        break
                    for content in update.contents:
                        if content.type == "text" and content.text:
                            log_stream_content("Agent", "text", {"text": content.text})
                            sys.stdout.write(content.text)
                            sys.stdout.flush()
                            turn_text += content.text
                        elif content.type == "function_call":
                            call_id = getattr(content, "call_id", None)
                            name = getattr(content, "name", None)
                            arguments = getattr(content, "arguments", "") or ""
                            log_stream_content("Agent", "function_call", {
                                "call_id": call_id, "name": name, "arguments": arguments
                            })
                            if call_id:
                                sys.stdout.write(f"\n\033[96m[Agent] Calling {name}...\033[0m\n")
                        elif content.type == "function_result":
                            call_id = getattr(content, "call_id", None)
                            result = getattr(content, "result", "")
                            log_stream_content("Agent", "function_result", {
                                "call_id": call_id, "result": str(result)
                            })
                    if getattr(update, "user_input_requests", None):
                        user_input_requests.extend(update.user_input_requests)
            except BaseException as e:
                from tools import QuotaAbortException
                if isinstance(e, QuotaAbortException) or type(e).__name__ == "QuotaAbortException":
                    sys.stdout.write(f"\n\033[91m[System] Task forcefully aborted: {str(e)}\033[0m\n")
                    break
                from engine.orchestrator import malformed_tool_call_nudge
                nudge = malformed_tool_call_nudge(e)
                if nudge and malformed_retries < 2:
                    malformed_retries += 1
                    sys.stdout.write(f"\n\033[93m[System] Model emitted a malformed tool call — retrying the turn ({malformed_retries}/2).\033[0m\n")
                    new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                    new_inputs.append(Message("user", [{"type": "text", "text": nudge}]))
                    current_input = new_inputs
                    has_requests = True
                    continue
                raise

            if context_budget and run_stream_chars > context_budget:
                if not budget_nudged:
                    # One wrap-up turn: no more research tools, write the artifacts NOW from what
                    # already exists. A second overshoot forces the completion check straight to
                    # its final verdict (same mechanism as max_run_minutes) — never nudge-loop.
                    budget_nudged = True
                    run_stream_chars = 0
                    req_artifact = config.cfg.get("settings", {}).get("workspace", {}).get("required_artifact", "final_report.md")
                    endgame = (
                        f"SYSTEM: you have reached your context budget for this run. Do NOT call "
                        f"delegate_tasks or any research tool again. Write findings.md (if missing) "
                        f"and '{req_artifact}' RIGHT NOW from the delegated results you already "
                        f"have, then stop. An incomplete but grounded report now beats a truncated "
                        f"context."
                    )
                    sys.stdout.write("\n\033[93m[System] Context budget reached — forcing wrap-up turn.\033[0m\n")
                    new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                    new_inputs.append(Message("user", [{"type": "text", "text": endgame}]))
                    current_input = new_inputs
                    has_requests = True
                    continue
                run_state.attempt = 10**6

            if user_input_requests:
                has_requests = True
                # current_input, not prompt: on a resumed run the two differ (resume preamble),
                # and rebuilding from `prompt` here would silently drop it.
                new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
                for req in user_input_requests:
                    is_approved = getattr(config, 'AUTO_APPROVE', False)
                    if is_approved:
                        sys.stdout.write(f"\n\033[93m[Agent] Auto-approving {req.function_call.name}...\033[0m\n")
                    else:
                        sys.stdout.write(f"\n\033[91m[Agent] Denied {req.function_call.name} (Auto-approve disabled).\033[0m\n")
                    new_inputs.append(Message("user", [req.to_function_approval_response(is_approved)]))
                current_input = new_inputs

            if not has_requests:
                def _cli_notify(msg: str):
                    plain = re.sub(r'\*\*', '', msg)
                    sys.stdout.write(f"\n\033[91m[System] {plain}\033[0m\n")
                    log_stream_content("Agent", "text", {"text": plain})

                if budget_deadline and time.monotonic() > budget_deadline:
                    _cli_notify(f"max_run_minutes ({max_run_minutes}) exceeded — no more retries; "
                                f"finishing with whatever exists (salvage still applies).")
                    # Forces run_completion_check straight past its retry branch into the
                    # final-verdict path (labeling + salvage), same as an exhausted attempt budget.
                    run_state.attempt = 10**6

                should_continue, current_input = await run_completion_check(
                    query=prompt, current_input=current_input, run_state=run_state, notify=_cli_notify,
                    last_assistant_text=turn_text,
                )
                if should_continue:
                    has_requests = True

        run_state.save()
        _write_log()
        elapsed = datetime.now() - start_time
        sys.stdout.write(f"\n\n\033[1mTask completed in {elapsed.total_seconds():.1f} seconds.\033[0m\n")

        # The one answer every headless run owes its user: which file to read, and whether the
        # run's environment/grounding stats mean it can be trusted at a glance.
        from utils.run_state import get_search_health
        req_artifact = config.cfg.get("settings", {}).get("workspace", {}).get("required_artifact", "final_report.md")
        report_path = os.path.join(os.path.abspath(_current_run_dir(run_dir_name)), req_artifact)
        health = get_search_health()
        if os.path.exists(report_path):
            sys.stdout.write(f"\033[1;32mReport:\033[0m {report_path}\n")
        else:
            sys.stdout.write("\033[1;31mReport: NOT WRITTEN\033[0m (see run folder for forensics)\n")
        sys.stdout.write(
            f"\033[2mRun folder:\033[0m {os.path.abspath(_current_run_dir(run_dir_name))}\n"
            f"\033[2mSources fetched:\033[0m {len(get_fetched_urls())}  "
            f"\033[2mweb_search failures:\033[0m {health['failures']}/{health['calls']}\n"
        )
    except Exception as e:
        sys.stdout.write(f"\n\033[91mError:\033[0m {e}\n")
        # A dead run must still leave its evidence behind and be detectable by exit code —
        # previously this path skipped run_state.save()/_write_log() and exited 0, so automation
        # couldn't tell a crashed run from a clean one (live case: the 2026-07-11 NIM 429 crash).
        if run_state is not None:
            run_state.sync_fetched_urls()
            run_state.save()
        _write_log()
        sys.exit(1)
    finally:
        tool_quotas_ctx.reset(quota_token)
        if run_state_token is not None:
            run_state_ctx.reset(run_state_token)
        if session_token is not None:
            from tools.fs import session_dir_ctx
            session_dir_ctx.reset(session_token)

def cli_main(builder):
    parser = argparse.ArgumentParser(description="DeepDelve TUI / CLI")
    parser.add_argument("--config", "-c", type=str, help="Path to config.yaml", default=None)
    parser.add_argument("--prompt", "-p", type=str, help="Run non-interactively with a specific prompt (headless mode)", default=None)
    parser.add_argument("--prompt-file", "-f", type=str, help="Run non-interactively reading a JSON context file", default=None)
    parser.add_argument("--auto-approve", action="store_true", help="Automatically approve all tool execution requests")
    parser.add_argument("--list-sessions", action="store_true", help="List saved sessions and exit")
    parser.add_argument("--resume", type=str, help="Resume a specific session by ID. Works in headless mode if --prompt is given, or in TUI mode otherwise.", default=None)
    parser.add_argument("--resume-run", type=str, default=None,
                        help="Continue an interrupted research run from its output folder (name or "
                             "path under the workspace dir). Reuses its fetched sources, findings "
                             "and plan instead of restarting; uses the run's original recorded "
                             "query unless --prompt is also given. Headless mode.")
    parser.add_argument("--depth", choices=["quick", "standard", "deep"], default=None,
                        help="Research depth preset: quick (~half quotas, light search, 2 retries), "
                             "standard (config as-is), deep (raised quotas, heavy search, 4 retries).")
    parser.add_argument("--style", choices=["standard", "academic"], default=None,
                        help="Report shape: standard (config as-is), academic (literature-review "
                             "paper with (Author, Year) citations and a References list — see "
                             "eval/sales_forecasting_benchmark.md). Orthogonal to --depth.")
    parser.add_argument("--seed-url", action="append", default=None, metavar="URL",
                        help="Pre-fetch this URL into the run's sources/ before research starts "
                             "(repeatable). Doesn't consume the agent's fetch quota. Headless mode.")
    parser.add_argument("--list-runs", action="store_true",
                        help="List research runs in the workspace dir (report status, date) and exit.")
    args, _ = parser.parse_known_args()

    import config
    config.AUTO_APPROVE = args.auto_approve

    if args.depth:
        apply_depth_preset(config.cfg, args.depth)

    if args.style:
        config.cfg.setdefault("settings", {})["report_style"] = args.style

    if args.list_runs:
        base = config.cfg.get("settings", {}).get("workspace", {}).get("dir", ".")
        req_artifact = config.cfg.get("settings", {}).get("workspace", {}).get("required_artifact", "final_report.md")
        if not os.path.isdir(base):
            sys.stdout.write(f"No runs found ({base} does not exist).\n")
            sys.exit(0)
        run_dirs = sorted(
            (d for d in Path(base).iterdir() if d.is_dir() and (d / "_run_state.json").exists()),
            key=os.path.getmtime, reverse=True,
        )
        if not run_dirs:
            sys.stdout.write(f"No runs found in {base}.\n")
            sys.exit(0)
        sys.stdout.write(f"Research runs in {os.path.abspath(base)} (newest first):\n")
        for d in run_dirs[:20]:
            ts = datetime.fromtimestamp(os.path.getmtime(d)).strftime("%Y-%m-%d %H:%M")
            has_report = (d / req_artifact).exists()
            status = "\033[32mreport\033[0m   " if has_report else "\033[31mNO REPORT\033[0m"
            sys.stdout.write(f"  {ts}  {status}  {d.name}\n")
        sys.exit(0)

    if args.list_sessions:
        log_dir = Path.home() / f".{config.APP_NAME}" / "sessions"
        if not log_dir.exists():
            sys.stdout.write("No sessions found.\n")
            sys.exit(0)
        files = sorted(log_dir.glob("session_*.json"), key=os.path.getmtime, reverse=True)
        if not files:
            sys.stdout.write("No sessions found.\n")
            sys.exit(0)
        sys.stdout.write("Saved Sessions:\n")
        import json
        for f in files[:10]:
            try:
                with open(f, "r") as fs:
                    j = json.load(fs)
                    ts = j.get("timestamp", "Unknown")
                    sid = j.get("session_id", f.stem.replace('session_', ''))
                    sys.stdout.write(f"- ID: {sid} (Date: {ts})\n")
            except Exception:
                sys.stdout.write(f"- Invalid session file: {f.name}\n")
        sys.exit(0)

    if args.prompt_file or args.prompt or args.resume_run:
        asyncio.run(run_cli(builder, prompt=args.prompt, prompt_file=args.prompt_file,
                            session_id=args.resume, resume_run=args.resume_run,
                            seed_urls=args.seed_url))
    else:
        BasicTuiAgent(builder, session_to_resume=args.resume).run()

if __name__ == "__main__":
    pass
