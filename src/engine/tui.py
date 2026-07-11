from datetime import datetime
from textual import work, on
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, Static, Collapsible, RichLog, Button
from textual.containers import VerticalScroll, Horizontal, Vertical
from rich.markdown import Markdown
from engine.orchestrator import create_local_agent, reset_session, delegation_depth_ctx, build_quota_pool, topup_quota_pool
import engine.orchestrator as orchestrator_module
import asyncio
import json
import config
from agent_framework import Message, Content
from textual import events
import os
import re
import uuid
import sys
import argparse
from pathlib import Path
from tools import tool_quotas_ctx, WORKSPACE_TOOLS, get_workspace_files, get_workspace_file_content
from tools.fs import _get_workspace_type, _get_workspace_dir
from utils.run_state import reset_fetched_urls, get_fetched_urls, record_fetched_url, RunState, run_state_ctx

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

    @work(exclusive=True)
    async def run_agent(self, query: str):
        self._is_agent_running = True

        # Session directory isolation: when enabled, ALL workspace file operations for this run
        # are transparently mapped to a subfolder named from the query + timestamp (e.g.
        # 'grasshopper_optimization_algorithm_20260710_192335/'), not a bare unix timestamp — see
        # _slugify_run_dir_name. Toggle via config.yaml: settings.workspace.session_isolation: true
        session_token = None
        run_dir_name = None
        if config.cfg.get("settings", {}).get("workspace", {}).get("session_isolation", False):
            from tools.fs import session_dir_ctx
            run_dir_name = _slugify_run_dir_name(query)
            session_token = session_dir_ctx.set(run_dir_name)

        # Initialize tool quotas from config
        quota_token = tool_quotas_ctx.set(build_quota_pool())
        reset_fetched_urls()
        run_state_token = None

        chat = self.query_one("#chat-container", VerticalScroll)
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
            run_state = RunState(_current_run_dir(run_dir_name))
            run_state.set_query(query)
            run_state_token = run_state_ctx.set(run_state)

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
                    new_inputs = [query] if isinstance(current_input, str) else list(current_input)

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

                if not has_requests:
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

DEFAULT_MAX_COMPLETION_CHECK_ATTEMPTS = 3


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


# Grounding-check logic (URL-presence gate + content-level claim gate) now lives in
# utils/grounding.py, shared with engine/orchestrator.py's upstream per-specialist check — see that
# module's header comment for why it isn't defined here.
from utils.grounding import (
    extract_cited_urls as _extract_cited_urls,
    extract_salient_terms as _extract_salient_terms,
    split_prose_from_sources as _split_prose_from_sources,
    claim_grounding_problem as _claim_grounding_problem,
    real_grounding_problem as _real_grounding_problem,
    fully_ungrounded as _fully_ungrounded,
)


def _quarantine_artifact(req_artifact: str, attempt: int) -> None:
    """Rename the bad artifact out of the model's visible workspace instead of just telling it to
    'overwrite' it. A small model that still sees its own wrong prior draft in the workspace tends
    to re-condition on it rather than truly restart — this removes that anchor."""
    try:
        from tools.fs import _get_safe_path
        path = _get_safe_path(req_artifact)
        if path and os.path.exists(path):
            os.rename(path, path + f".rejected_attempt_{attempt}")
    except Exception:
        pass


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


def _salvage_narrated_report(req_artifact: str, last_assistant_text: str) -> bool:
    """Structural fallback for a real, recurring pattern (documented in the reference project too,
    surviving multiple rounds of prompt-only fixes there): the model narrates a complete,
    well-formatted report as chat text instead of ever calling write_workspace_file, across the
    entire retry budget. Rather than throw away real content because a specific tool call didn't
    fire, auto-persist the model's own last substantial response — clearly marked as unverified
    salvage, not a substitute for the grounding check. Returns True if a salvage write happened."""
    if not last_assistant_text or len(last_assistant_text.strip()) < 200:
        return False
    try:
        from tools.fs import _get_safe_path
        path = _get_safe_path(req_artifact)
        if not path:
            return False
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        salvage = (
            "> **AUTO-RECOVERED DRAFT** — the model narrated this content as chat text instead of "
            "calling `write_workspace_file`, across the full retry budget. This has NOT passed the "
            "grounding check and its claims are UNVERIFIED. Review before trusting it.\n\n"
            + last_assistant_text.strip()
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(salvage)
        return True
    except Exception:
        return False


async def run_completion_check(query: str, current_input, run_state: "RunState", notify, last_assistant_text: str = ""):
    """Runs the 3-tier completion check (delegated? artifact exists? really grounded?) plus the
    structural fixes: per-attempt quota top-up, artifact quarantine, run-state persistence, and
    (as a last resort) salvaging a narrated-but-never-written report instead of losing it.

    Returns (should_retry: bool, new_current_input). Caller is responsible for looping while
    should_retry is True, same as before.
    """
    req_artifact = config.cfg.get("settings", {}).get("workspace", {}).get("required_artifact", None)
    if not req_artifact:
        return False, current_input

    # Configurable, not hardcoded — the fixed default of 3 was cutting runs off with real sources
    # sitting unused in findings.md, well before hardware was anywhere near a real constraint
    # (confirmed live: an 11-source run exhausted its budget at ~11% system memory usage while the
    # model still hadn't complied with two explicit "add real citation links" nudges in a row).
    # Raising this trades wall-clock time and tool-call quota for more chances to self-correct.
    MAX_COMPLETION_CHECK_ATTEMPTS = config.cfg.get("settings", {}).get(
        "max_completion_check_attempts", DEFAULT_MAX_COMPLETION_CHECK_ATTEMPTS
    )

    attempt = run_state.attempt

    try:
        quotas = tool_quotas_ctx.get()
        delegated = bool(quotas and quotas.get("delegate_tasks", {}).get("used", 0) > 0)
        files = get_workspace_files()
        content = get_workspace_file_content(req_artifact) if req_artifact in files else None

        # Detecting the problem (or lack of one) never consumes the retry budget —
        # only actually retrying does. Otherwise a success on the final allowed
        # attempt is never recognized as a success (it just falls through silently).
        if not delegated:
            is_last_chance = (attempt + 1) >= MAX_COMPLETION_CHECK_ATTEMPTS
            todos_used = (quotas or {}).get("write_todos", {}).get("used", 0)
            # A real, live-observed failure mode distinct from every other one fixed so far: the
            # Planner writes/rewrites _todos.md across every nudge (satisfying "take an action" with
            # write_todos instead of delegate_tasks) and answers from its own memory — sometimes
            # explicitly narrating fake delegation that never happened, e.g. literally writing
            # "After delegating the tasks to a human Searcher, here's what I've found:" despite
            # delegate_tasks never once appearing in the tool-call log. Generic "you must verify"
            # wording didn't stop this in testing; naming the specific wrong action (rewriting the
            # plan, fabricating delegation narration) does, per the same pattern that fixed the
            # missing_artifact re-delegation loop.
            repeated_planning = todos_used >= 2
            escalation = ""
            if repeated_planning:
                escalation = (
                    f" You have called write_todos {todos_used} times but delegate_tasks ZERO times — "
                    f"rewriting the plan is not research and does not satisfy this requirement. Do NOT "
                    f"call write_todos again. Do NOT write a report claiming you delegated or received "
                    f"results from a Searcher when delegate_tasks was never actually called — that is "
                    f"fabrication, not synthesis."
                )
            last_chance_prefix = "THIS IS YOUR FINAL ATTEMPT. " if is_last_chance else ""
            problem, warning_msg, inject_msg = "not_delegated", \
                "No `delegate_tasks` call was ever made — this looks like an answer from memory, not real research. Forcing verification.", \
                f"SYSTEM WARNING: {last_chance_prefix}You are attempting to finish the task, but you never called delegate_tasks. Your training data can be stale or wrong — you MUST verify any facts with a real Searcher delegation before finishing.{escalation} Your ONLY next tool call must be delegate_tasks, with a real task_name/instructions/agent_id for each research angle. Only after receiving real results should you write (or overwrite) '{req_artifact}'."
        elif (
            config.cfg.get("settings", {}).get("grounding_check", {}).get("check_findings", True)
            and "findings.md" in files
            and (findings_problem := _fully_ungrounded(get_workspace_file_content("findings.md") or ""))
        ):
            # findings.md (Pass 1) was previously never grounding-checked at all — only
            # final_report.md was. Confirmed live: a Planner that abandons real delegation partway
            # through a run can fabricate the ENTIRE Pass-1 file from memory, and Pass 2 then
            # treats it as ground truth (SESSION_STATUS.md tracked item #2). Checked BEFORE the
            # missing-artifact/final-report gates because fabricated findings poison everything
            # downstream — a final report rewritten from fabricated findings can never become
            # grounded. Uses the wholesale-fabrication gate (fully_ungrounded), not the strict
            # per-URL one, so legitimately-mixed Pass-1 notes don't hard-fail a run.
            problem, warning_msg, inject_msg = "findings_ungrounded", \
                f"`findings.md` (Pass 1) fails the grounding check ({findings_problem}) — nothing in it traces to a source actually fetched this run. Pushing agent to rebuild it from real delegated results.", \
                f"SYSTEM WARNING: your Pass-1 'findings.md' is not grounded in real research ({findings_problem}) — " + \
                ("it contains no source URLs at all" if findings_problem == "no_urls" else "not one URL it cites matches anything your Searcher(s) actually fetched this run") + \
                f". findings.md must be a verbatim consolidation of what your delegated Searchers/Analyzers actually returned, never written from your own memory. The fabricated file has been moved aside. Delegate real research tasks now if you haven't, then rebuild findings.md strictly from those real results — only after that, write '{req_artifact}' from it."
        elif req_artifact not in files:
            # This `elif` header (and its is_last_chance) was accidentally swallowed when the
            # findings gate above was inserted (bd307f4), merging two branches: the
            # findings_ungrounded assignment was silently overwritten by missing_artifact, and
            # referencing is_last_chance crashed with UnboundLocalError — confirmed live
            # 2026-07-11, it ended a benchmark run one nudge early.
            is_last_chance = (attempt + 1) >= MAX_COMPLETION_CHECK_ATTEMPTS
            # A model that already has real delegated research results in its own context but still
            # hasn't written the artifact tends to respond to a generic nudge by re-delegating again
            # (a real failure mode observed in testing: it satisfies "take a real action" with
            # delegate_tasks instead of write_workspace_file). Naming and forbidding that specific
            # wrong action, rather than only naming the right one, measurably changes behavior on
            # small models — same principle as the existing Anti-Looping prompt rules, applied
            # structurally here since the prompt-level rule alone didn't hold under a nudge.
            forbid_redelegate = (
                " You already have research results above from your delegated task(s) — do NOT call "
                "delegate_tasks again. Your ONLY next action must be write_workspace_file."
                if delegated else ""
            )
            last_chance_prefix = "THIS IS YOUR FINAL ATTEMPT. " if is_last_chance else ""
            problem, warning_msg, inject_msg = "missing_artifact", \
                f"Required artifact `{req_artifact}` is missing from the workspace. Pushing agent to create it.", \
                f"SYSTEM WARNING: {last_chance_prefix}You are attempting to finish the task, but the required final artifact '{req_artifact}' is missing from the workspace. Writing your answer as a chat message does NOT complete the task.{forbid_redelegate} Call write_workspace_file(filename='{req_artifact}', content=...) right now, using whatever findings you already have — an imperfect report that exists beats a perfect one that doesn't."
        else:
            grounding_problem = await _real_grounding_problem(content or "")

            # Structural signal for a real, confirmed failure mode: a model makes ONE
            # delegate_tasks call early on (satisfying "you must delegate"), then — after a
            # grounding-check rejection — just rewrites the SAME report from memory with different
            # fake citations instead of ever delegating again, because the existing nudges all
            # phrase the fix as "rewrite using what you have," which quietly assumes enough real
            # findings already exist. Confirmed live: a 9-attempt run with fetched_url_count stuck
            # at 2 the entire time, one delegate_tasks call total, ending in salvage. Detected here
            # deterministically (no new fetches since the last completion check) rather than
            # guessed from wording, and used below to make the redelegation instruction explicit
            # instead of implicit.
            prior_attempts = run_state.data.get("completion_check_attempts", [])
            no_new_fetches = bool(prior_attempts) and prior_attempts[-1].get("fetched_url_count") == len(get_fetched_urls())
            redelegate_directive = (
                " You have NOT fetched any new sources since your last attempt — rewriting the "
                "report with the same information will fail the exact same way again. Your ONLY "
                "next tool call must be delegate_tasks, with real research tasks covering the "
                "specific claims or sectors that don't have a grounded source yet. Do NOT call "
                "write_workspace_file again until you have new, real findings to write from."
                if no_new_fetches else ""
            )

            if grounding_problem and grounding_problem.startswith("claim_unsupported"):
                # Distinct from "not_grounded": the URL WAS actually fetched — the problem is that
                # the report's claims don't appear to come from what that source actually says. The
                # right correction is different too: re-read the source and use what it actually
                # says, not re-delegate for a new URL (which the not_grounded message would suggest).
                problem, warning_msg, inject_msg = "claim_unsupported", \
                    f"`{req_artifact}` cites a source that was fetched, but the claims near it don't appear to come from that source's actual content ({grounding_problem}). Pushing agent to re-check.", \
                    f"SYSTEM WARNING: '{req_artifact}' cites at least one source that WAS actually fetched ({grounding_problem}), but the specific claims attributed to it don't share any checkable fact (number, name, or figure) with what that source actually contains. This looks like the source was cited without being read, or the claim was written from memory and a real citation was attached to it afterward. The previous draft has been moved aside. Before rewriting: delegate re-reading of that exact fetched file to an Analyzer if you haven't already, and only state what the Analyzer's findings actually say — do not keep the same claim and just hope the citation makes it look sourced."
            elif grounding_problem == "no_urls":
                # Distinct from "cited a URL that wasn't fetched": here there are no citations AT
                # ALL, not a wrong one — the generic "cites at least one URL that does not match"
                # message doesn't even make sense for this case, and a live test showed a model
                # get this generic nudge 3 times in a row without ever adapting (it kept naming
                # sources in prose without ever hyperlinking them). Escalates on repeat, same
                # pattern as the not_delegated/missing_artifact escalations.
                no_urls_count = run_state.data.get("no_urls_count", 0) + 1
                run_state.data["no_urls_count"] = no_urls_count
                escalation = ""
                if no_urls_count >= 2:
                    # Words alone didn't work the first time ("add real citation links" was
                    # already said once) — handing back the exact URL list removes any excuse to
                    # keep failing the same way. Confirmed live: a model that failed this same
                    # check twice in a row, both times with real sources already sitting in its
                    # own findings, never once copied one in on its own.
                    real_urls = get_fetched_urls()
                    url_list = "\n".join(f"- {u['url']}" for u in real_urls[:20]) or "(none fetched yet)"
                    escalation = (
                        f" This is the {no_urls_count}th time in a row you have written this report "
                        f"with ZERO hyperlinked sources. Naming a source in prose (e.g. \"(World Bank, "
                        f"2020)\") does NOT count as a citation. Here are the EXACT URLs actually "
                        f"fetched this run — use these, copied verbatim, do not paraphrase or "
                        f"invent your own:\n{url_list}\nEvery single claim must end with a real "
                        f"markdown link `[Title](URL)` using one of the URLs above."
                    )
                is_last_chance = (attempt + 1) >= MAX_COMPLETION_CHECK_ATTEMPTS
                last_chance_prefix = "THIS IS YOUR FINAL ATTEMPT. " if is_last_chance else ""
                problem, warning_msg, inject_msg = "not_grounded", \
                    f"`{req_artifact}` contains zero hyperlinked sources — no citations at all. Pushing agent to add real ones.", \
                    f"SYSTEM WARNING: {last_chance_prefix}'{req_artifact}' does not contain a single `[Title](URL)` link anywhere — you named sources in prose but never actually cited them. The previous draft has been moved aside. Rewrite '{req_artifact}' using the exact format `- **[Title](URL)**` for every source, with real URLs your Searcher(s) actually returned in their findings.{escalation}{redelegate_directive}"
            elif grounding_problem and grounding_problem.startswith("non_url_citation"):
                # Distinct from "no_urls": the report DOES have real hyperlinked citations
                # elsewhere (that's why it reached this branch instead of "no_urls" above), but at
                # least one OTHER claim is attributed to something that isn't a URL at all — a bare
                # "(DANE, 2020)"-style parenthetical or a "Source: <prose>" line. This evades the
                # URL-presence check entirely (extract_cited_urls never sees a non-URL attribution),
                # so a report can look grounded overall while still smuggling in an unverifiable
                # claim — confirmed live (SESSION_STATUS.md's tracked #1 open item at the time).
                problem, warning_msg, inject_msg = "non_url_citation", \
                    f"`{req_artifact}` attributes at least one claim to something that isn't a real URL ({grounding_problem}) — pushing agent to fix it.", \
                    f"SYSTEM WARNING: '{req_artifact}' attributes at least one claim to a non-URL citation ({grounding_problem}) — e.g. a bare parenthetical like \"(DANE, 2020)\" or a \"Source: <description>\" line with no link. This is exactly as unverifiable as a fabricated URL — there is nothing to check it against. The previous draft has been moved aside. Every single claim must end with a real, hyperlinked `[Title](URL)` using a URL your Searcher(s) actually returned this run. If you don't have a real fetched URL for a specific claim, either delegate to get one or remove the claim entirely — do not attribute it to an organization name, a year, or a vague description instead.{redelegate_directive}"
            elif grounding_problem:
                problem, warning_msg, inject_msg = "not_grounded", \
                    f"`{req_artifact}` cites a URL that was never actually fetched this run ({grounding_problem}) — this looks ungrounded or hallucinated. Pushing agent to fix citations.", \
                    f"SYSTEM WARNING: '{req_artifact}' cites at least one URL that does not match anything your Searcher(s) actually fetched this run ({grounding_problem}). This is a strong signal of a hallucinated source. The previous draft has been moved aside — write a fresh '{req_artifact}' using ONLY URLs your Searcher(s) actually returned in their findings. If you don't have a real source for a claim, delegate again and use exactly what comes back, not your own prior knowledge.{redelegate_directive}"
            else:
                problem = None

        run_state.sync_fetched_urls()
        run_state.record_attempt(attempt, problem, len(get_fetched_urls()))

        if problem and attempt < MAX_COMPLETION_CHECK_ATTEMPTS:
            run_state.attempt = attempt + 1

            notify(f"**System ({attempt + 1}/{MAX_COMPLETION_CHECK_ATTEMPTS}):** {warning_msg}")

            if problem in ("not_grounded", "claim_unsupported", "non_url_citation"):
                _quarantine_artifact(req_artifact, attempt + 1)
            elif problem == "findings_ungrounded":
                _quarantine_artifact("findings.md", attempt + 1)

            # Per-attempt quota top-up: without this, a retry shares the same already-exhausted
            # pool as the failed attempt it's correcting (see plan doc diagnosis point 2) and
            # structurally can't recover on a complex query.
            pool = tool_quotas_ctx.get()
            if pool is not None:
                topup_quota_pool(pool)

            new_inputs = [current_input] if isinstance(current_input, str) else list(current_input)
            new_inputs.append(Message("user", [{"type": "text", "text": inject_msg}]))
            run_state.save()
            return True, new_inputs

        if problem:
            # Retry budget is exhausted and a real problem still exists. The old project silently
            # accepted whatever was left at this point with no indication to the user that the output
            # is unverified or even absent — a genuinely observed failure mode in testing (both
            # "wrote something ungrounded" and, separately, "never wrote anything at all" have been
            # seen live), not a hypothetical one. Surface exactly which case this is instead of
            # asserting a file exists when it might not.
            # Name a sick search layer explicitly — confirmed live (2026-07-11): DDG throttling made
            # two different models' runs fail in ways that looked exactly like model fabrication.
            from utils.run_state import get_search_health
            health = get_search_health()
            if health["calls"] >= 4 and health["failures"] * 2 >= health["calls"]:
                notify(f"**System (final):** ⚠️ web_search failed {health['failures']}/{health['calls']} "
                       f"times this run (throttling or outage) — this failure is likely environmental, "
                       f"not a model problem. Re-run later before drawing conclusions about the model.")
            if req_artifact in get_workspace_files():
                notify(f"**System (final):** Retry budget exhausted with an unresolved issue ({problem}). "
                       f"`{req_artifact}` exists but could NOT be fully verified this run — treat its "
                       f"claims as unconfirmed. This was not silently accepted.")
            elif problem == "missing_artifact" and _salvage_narrated_report(req_artifact, _find_last_substantial_text() or last_assistant_text):
                # Structural fallback, not another prompt nudge — see _salvage_narrated_report's
                # docstring for why: nudging alone has proven insufficient for this exact pattern
                # across two independent projects now.
                notify(f"**System (final):** The model never called write_workspace_file despite "
                       f"repeated nudges, but had already narrated a substantial response. "
                       f"Auto-recovered it into `{req_artifact}`, clearly marked as unverified salvage "
                       f"content — this bypassed the grounding check entirely and MUST be reviewed "
                       f"before trusting it.")
            else:
                notify(f"**System (final):** Retry budget exhausted with an unresolved issue ({problem}). "
                       f"`{req_artifact}` was never written — no report was produced this run. This was "
                       f"not silently accepted as a success.")

        run_state.set_plan(get_workspace_file_content("_todos.md") or "")
        run_state.save()
        return False, current_input
    except Exception:
        # Deliberately non-fatal (a crashed CHECK must never kill a run that produced work), but
        # never silent again — this bare swallow hid a real completion-check crash on a live
        # benchmark run (2026-07-11), which then looked like a model that just stopped retrying.
        import traceback
        notify(f"**System:** completion check itself crashed — run ends unverified. This is an "
               f"engine bug, not a model failure:\n```\n{traceback.format_exc()}\n```")
        return False, current_input


async def run_cli(builder, prompt: str = None, prompt_file: str = None, session_id: str = None):
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

    # Deferred until here (not right at function start) so a --prompt-file run's folder name is
    # slugified from the actual resolved prompt text, not generated before it's known.
    if config.cfg.get("settings", {}).get("workspace", {}).get("session_isolation", False):
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

    sys.stdout.write(f"\n\033[1mStarting task:\033[0m {prompt[:100]}...\n\n")
    start_time = datetime.now()

    run_state = None
    try:
        from agent_framework import Message
        current_input = prompt
        has_requests = True
        run_state = RunState(_current_run_dir(run_dir_name))
        run_state.set_query(prompt)
        run_state_token = run_state_ctx.set(run_state)
        # Written immediately (not only at the first completion check / clean run end) so a crash
        # or power loss mid-research still leaves a forensic _run_state.json behind — confirmed
        # live 2026-07-11: a NIM 429 killed a run 10 minutes in and left 15 fetched files with no
        # state record at all, making the run unscoreable.
        run_state.save()
        malformed_retries = 0

        while has_requests:
            has_requests = False
            user_input_requests = []
            turn_text = ""

            try:
                stream = agent.run(current_input, session=session, stream=True)
                async for update in stream:
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

            if user_input_requests:
                has_requests = True
                new_inputs = [prompt] if isinstance(current_input, str) else list(current_input)
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
    args, _ = parser.parse_known_args()

    import config
    config.AUTO_APPROVE = args.auto_approve

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

    if args.prompt_file:
        asyncio.run(run_cli(builder, prompt_file=args.prompt_file, session_id=args.resume))
    elif args.prompt:
        asyncio.run(run_cli(builder, prompt=args.prompt, session_id=args.resume))
    else:
        BasicTuiAgent(builder, session_to_resume=args.resume).run()

if __name__ == "__main__":
    pass
