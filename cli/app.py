import os
import sys
import re
import json
import shlex
import uuid
import threading
import time
from pathlib import Path
from datetime import datetime

_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
os.chdir(_project_root)

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static, Input, Markdown, Select, Tree
from textual.containers import VerticalScroll, Horizontal, Container, Vertical
from textual import on, work
from textual.events import Click

from chat_manager import ChatManager
from config_manager import ConfigManager
from message_compaction import MessageCompactor
from branch_manager import BranchManager
from branch_db import BranchDatabase
from folder_manager import FolderManager
from prompt_templates import get_default_prompt
from api_clients import estimate_tokens
from cost_tracker import calculate_cost as calc_api_cost
from error_classifier import LLMApiError, classify_error, friendly_message, ErrorClass
from context_documents import context_documents, save_document, delete_document

import requests

from api_clients import (
    call_groq_stream,
    call_google_stream,
    call_mistral_stream,
    call_openrouter_stream,
    call_llamacpp_stream,
    call_openai_compat_stream,
    fetch_openai_compat_catalog,
)

from tools import tool_registry, ToolSafetyManager

from cli.themes import get_theme_css


CSS = """
Screen {
    background: #1a1a2e;
}

#main-layout {
    layout: horizontal;
    height: 1fr;
}

#left-sidebar {
    width: 30;
    background: #16213e;
    border: solid #444444;
    display: block;
}

#left-sidebar.hidden {
    display: none;
}

#center-panel {
    width: 1fr;
    background: #1a1a2e;
    layout: vertical;
}

#chat-display {
    height: 1fr;
}

#right-sidebar {
    width: 34;
    background: #16213e;
    border: solid #444444;
    display: none;
}

#right-sidebar.visible {
    display: block;
}

#header {
    dock: top;
    height: 3;
    background: #16213e;
    color: #aaaaaa;
    padding: 0 1;
    layout: horizontal;
}

#header-title {
    width: auto;
    content-align: left middle;
    color: #ffffff;
    text-style: bold;
}

#header-dropdowns {
    width: 1fr;
    layout: horizontal;
    content-align: left middle;
    padding: 0 1;
}

#header-dropdowns Select {
    width: 1fr;
    margin: 0 1;
    height: 1;
}

#input-bar {
    dock: bottom;
    height: 3;
    background: #16213e;
    border-top: solid #444444;
    padding: 0 1;
}

#input-bar Input {
    width: 1fr;
}

#footer {
    dock: bottom;
    height: 1;
    background: #16213e;
    color: #888888;
    content-align: left middle;
}

.sidebar-section-title {
    color: #5f87ff;
    text-style: bold;
    text-align: left;
}

.sidebar-item {
    color: #aaaaaa;
}

.sidebar-item.active {
    color: #5f87ff;
    text-style: bold;
}

.folder-item {
    color: #d4a5ff;
    text-style: bold;
}

.chat-item {
    color: #aaaaaa;
}

.chat-item.active {
    color: #5f87ff;
    text-style: bold;
}

.setting-value {
    color: #cccccc;
}

#folder-tree {
    background: transparent;
}

#folder-tree > .tree--cursor {
    background: #2a2a4a;
}

#chat-list {
    background: transparent;
}

#chat-list > .tree--cursor {
    background: #2a2a4a;
}

.message-turn {
    margin: 1 2;
    padding: 0;
    width: 1fr;
}

.message-separator {
    color: #333355;
    text-align: left;
    height: 1;
}

.user-label {
    color: #5f87ff;
    text-style: bold;
}

.assistant-label {
    color: #87d787;
    text-style: bold;
}

.user-content {
    color: #e0e0e0;
}

.message-bubble-user {
    background: #1e2f5a;
    border: round #2f5fa7;
    padding: 1 2;
}

.message-bubble-assistant {
    background: #18203f;
    border: round #365b8f;
    padding: 1 2;
}

.assistant-content {
    color: #e6ebff;
}

#chat-display Markdown {
    color: #e6ebff;
    background: transparent;
}
"""


class ThreadBearApp(App):
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+l", "toggle_left", "Toggle Sidebar"),
        Binding("ctrl+r", "toggle_right", "Toggle Settings"),
        Binding("ctrl+d", "quit", "Quit"),
        Binding("escape", "focus_input", "Focus Input"),
    ]

    def __init__(self):
        super().__init__()
        self.project_root = Path(__file__).parent.parent
        
        self.config = ConfigManager(config_file=str(self.project_root / "config.json"))
        self.config.migrate_llamacpp_saved_urls()

        self.chat_manager = ChatManager(chats_directory=str(self.project_root / "chats"))
        self.compactor = MessageCompactor(config_manager=self.config)
        self.branch_db = BranchDatabase(db_path=str(self.project_root / "threadbear_docs.db"))
        self.branch_manager = BranchManager(self.branch_db)
        self.folder_manager = FolderManager(chats_directory=str(self.project_root / "chats"))
        self.chat_manager.branch_db = self.branch_db
        self.branch_db.migrate_from_json(self.chat_manager.chats_directory)

        self.builtin_providers = ["groq", "google", "mistral", "openrouter", "llamacpp"]

        # Known OpenAI-compatible providers: name slug → base_url + context_window defaults
        self.known_providers = {
            "cerebras": {"base_url": "https://api.cerebras.ai/v1", "context_window": 131072},
        }
        
        self._show_left = True
        self._show_right = False
        self._streaming = False
        self._cancel_event = threading.Event()
        self.temporary_mode = False
        self.incognito_mode = False
        
        theme_name = self.config.get("cli_theme", "dark")
        theme_css = get_theme_css(theme_name)
        if theme_css:
            self.CSS = theme_css + "\n" + CSS

    def on_mount(self):
        self.push_screen(MainScreen())

    def action_toggle_left(self):
        self._show_left = not self._show_left
        try:
            left_sidebar = self.screen.query_one("#left-sidebar")
            if self._show_left:
                left_sidebar.remove_class("hidden")
            else:
                left_sidebar.add_class("hidden")
        except Exception:
            pass

    def action_toggle_right(self):
        self._show_right = not self._show_right
        try:
            right_sidebar = self.screen.query_one("#right-sidebar")
            if self._show_right:
                right_sidebar.add_class("visible")
            else:
                right_sidebar.remove_class("visible")
        except Exception:
            pass

    def action_focus_input(self):
        try:
            input_widget = self.screen.query_one("#chat-input", Input)
            input_widget.focus()
        except Exception:
            pass

    @property
    def available_providers(self):
        return self.builtin_providers + list(self.known_providers.keys()) + list(self.config.get("custom_endpoints", {}).keys())

    def _get_stream_func(self, provider):
        builtin = {
            "groq": call_groq_stream,
            "google": call_google_stream,
            "mistral": call_mistral_stream,
            "openrouter": call_openrouter_stream,
            "llamacpp": call_llamacpp_stream,
        }
        if provider in builtin:
            return builtin[provider]
        if provider in self.known_providers:
            return call_openai_compat_stream
        if provider in self.config.get("custom_endpoints", {}):
            return call_openai_compat_stream
        return None

    def _inject_endpoint_config(self, provider, merged_cfg):
        if provider in self.known_providers:
            ep = self.known_providers[provider]
            api_key = self.config.get_api_key(provider)
            merged_cfg["_endpoint_base_url"] = ep["base_url"]
            merged_cfg["_endpoint_api_key"] = api_key
            merged_cfg["_endpoint_provider"] = provider
        endpoints = self.config.get("custom_endpoints", {})
        if provider in endpoints:
            ep = endpoints[provider]
            api_key = self.config.get_api_key(provider)
            merged_cfg["_endpoint_base_url"] = ep["base_url"]
            merged_cfg["_endpoint_api_key"] = api_key
            merged_cfg["_endpoint_provider"] = provider

    def _get_llamacpp_url(self):
        return self.config.get("llamacpp_url", "http://localhost:8080")

    @work(thread=True)
    def run_chat_turn(self, user_message: str):
        if not self.chat_manager.current_chat_file:
            self.chat_manager.create_new_chat()

        if not self.incognito_mode:
            self.chat_manager.add_message("user", user_message, auto_save=False)

        api_messages = self.chat_manager.get_conversation_context()

        docs = context_documents.build_context_injections()
        api_messages.extend(docs)

        provider = self.config.get("provider")
        model = self.config.get(f"{provider}_model")
        model_settings = self.config.get_model_settings(provider, model)

        temperature = float(
            model_settings.get('temperature',
                self.config.get(f"{provider}_temperature", 0.7))
        )
        max_tokens = int(
            model_settings.get('max_tokens',
                self.config.get(f"{provider}_max_tokens", 4096))
        )
        system_prompt = self.config.get_system_prompt(provider, model)

        merged_cfg = dict(self.config.config)
        if provider == "llamacpp":
            merged_cfg["llamacpp_url"] = self._get_llamacpp_url()
        self._inject_endpoint_config(provider, merged_cfg)
        merged_cfg.update({
            "model": model,
            f"{provider}_model": model,
            f"{provider}_temperature": temperature,
            f"{provider}_system_prompt": system_prompt,
            f"{provider}_max_tokens": max_tokens,
            "temperature": temperature,
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
        })
        if "top_p" in model_settings:
            merged_cfg["top_p"] = model_settings["top_p"]
        if "top_k" in model_settings:
            merged_cfg["top_k"] = model_settings["top_k"]

        tool_config = self.config.get_tool_config(provider)
        tools_enabled = tool_config.get('enabled', False)
        tool_schemas = tool_registry.get_schemas_for_provider() if tools_enabled else None
        safety_mgr = ToolSafetyManager({
            'blocked_commands': tool_config.get('blocked_commands', []),
            'tool_workspace': tool_config.get('workspace'),
        }) if tools_enabled else None
        max_iterations = tool_config.get('max_iterations', 5)

        if tools_enabled and tool_schemas:
            tool_names = [t['function']['name'] for t in tool_schemas]
            tool_os = self.config.get('tool_os', 'windows')
            os_hints = {
                'windows': "The user is on Windows. Use Windows commands (PowerShell/cmd), Windows file paths (backslashes), and Windows-compatible tools.",
                'linux': "The user is on Linux. Use Bash/shell commands, Unix file paths (forward slashes), and Linux-compatible tools.",
                'macos': "The user is on macOS. Use Bash/zsh commands, Unix file paths (forward slashes), and macOS-compatible tools.",
            }
            tool_hint = (
                "\n\nYou have access to the following tools: "
                + ", ".join(tool_names) + ". "
                + os_hints.get(tool_os, os_hints['windows']) + " "
                "When the user asks you to write files, run commands, list directories, "
                "or perform actions on their system, USE the tools directly instead of "
                "just showing code. Execute the actions using your tools."
            )
            if 'web_search' in tool_names:
                tool_hint += (
                    "\n\nWEB SEARCH STRATEGY: For complex or multi-faceted questions, "
                    "break the question into 2-4 focused sub-queries and search each separately."
                )
            project_root = str(self.project_root)
            tool_hint += (
                f"\n\nPROJECT PATHS:"
                f"\n- Project root: {project_root}"
                f"\n- Toolbox: {os.path.join(project_root, 'toolbox')}"
            )
            system_prompt = (system_prompt or "") + tool_hint
            merged_cfg["system_prompt"] = system_prompt
            merged_cfg[f"{provider}_system_prompt"] = system_prompt

        stream_func = self._get_stream_func(provider)
        if not stream_func:
            self.call_from_thread(
                self._append_error, f"Unknown provider: {provider}"
            )
            return

        self._streaming = True
        full_response = ""
        stream_usage = None
        tool_events_log = []
        working_texts = []
        max_overflow_retries = 2

        try:
            for iteration in range(max_iterations):
                if iteration > 0:
                    time.sleep(2)
                    slim_messages = []
                    for m in api_messages:
                        if m.get('role') == 'system':
                            slim_messages.append(m)
                        else:
                            break
                    last_user = None
                    for m in api_messages:
                        if m.get('role') == 'user':
                            last_user = m
                    if last_user:
                        slim_messages.append(last_user)
                    empty_tool_ids = set()
                    for m in api_messages:
                        if m.get('role') == 'tool':
                            try:
                                content = json.loads(m.get('content', '{}'))
                                if content.get('no_results'):
                                    empty_tool_ids.add(m.get('tool_call_id', ''))
                                    continue
                            except (json.JSONDecodeError, AttributeError):
                                pass
                            slim_messages.append(m)
                        elif m.get('tool_calls'):
                            if empty_tool_ids:
                                kept = [tc for tc in m['tool_calls'] if tc.get('id', '') not in empty_tool_ids]
                                if kept:
                                    trimmed = dict(m)
                                    trimmed['tool_calls'] = kept
                                    slim_messages.append(trimmed)
                            else:
                                slim_messages.append(m)
                    api_messages = slim_messages

                tool_calls_this_round = []
                content_buffer = ""

                for overflow_attempt in range(max_overflow_retries + 1):
                    try:
                        tool_calls_this_round = []
                        content_buffer = ""

                        try:
                            if self.compactor.should_compact(api_messages, provider, model):
                                compacted, summary = self.compactor.compact_messages(
                                    api_messages, provider, model
                                )
                                api_messages = compacted
                                print(f"[Compaction] Applied: {summary[:50]}")
                        except Exception as compact_err:
                            print(f"Pre-LLM compaction failed (non-blocking): {compact_err}")

                        for chunk in stream_func(api_messages, merged_cfg, tools=tool_schemas):
                            if self._cancel_event.is_set():
                                self._cancel_event.clear()
                                self.call_from_thread(self._append_error, "Cancelled")
                                return

                            if isinstance(chunk, dict) and chunk.get('type') == 'tool_calls':
                                tool_calls_this_round = chunk.get('tool_calls', [])
                            elif isinstance(chunk, dict) and chunk.get('type') == 'usage':
                                stream_usage = chunk
                            elif isinstance(chunk, str):
                                content_buffer += chunk
                                self.call_from_thread(self._append_streaming, chunk)
                        break
                    except LLMApiError as overflow_err:
                        cls = classify_error(overflow_err.status_code, overflow_err.response_text)
                        if cls == ErrorClass.CONTEXT_OVERFLOW and overflow_attempt < max_overflow_retries:
                            self.call_from_thread(
                                self._append_status, "Context too large, compacting..."
                            )
                            compacted, _ = self.compactor.compact_messages(
                                api_messages, provider, model, force=True
                            )
                            api_messages = compacted
                            content_buffer = ""
                            tool_calls_this_round = []
                            continue
                        raise

                if not tool_calls_this_round:
                    full_response = content_buffer
                    break

                if content_buffer and content_buffer.strip():
                    working_texts.append(content_buffer.strip())

                assistant_msg = {'role': 'assistant', 'content': content_buffer or None}
                if tool_calls_this_round:
                    assistant_msg['tool_calls'] = tool_calls_this_round
                api_messages.append(assistant_msg)

                for tc in tool_calls_this_round:
                    name = tc.get('function', {}).get('name', '')
                    try:
                        args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                    except json.JSONDecodeError:
                        args = {}

                    self.call_from_thread(self._append_tool_event, name, args)
                    tool_events_log.append({'name': name, 'args': args, 'status': 'running', 'result': None})

                    result = tool_registry.execute_tool(name, args, safety_mgr)

                    self.call_from_thread(self._append_tool_result, name, result)
                    for te in reversed(tool_events_log):
                        if te['name'] == name and te['status'] == 'running':
                            te['status'] = 'success' if result.get('success', True) else 'error'
                            te['result'] = result
                            break

                    is_empty = (
                        result.get('error')
                        or (name == 'web_search' and not result.get('scraped') and not result.get('results'))
                    )
                    if is_empty:
                        stub = {'no_results': True}
                        if result.get('error'):
                            stub['error'] = result['error']
                        if result.get('message'):
                            stub['message'] = result['message']
                        api_messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.get('id', ''),
                            'content': json.dumps(stub)
                        })
                    else:
                        try:
                            ctx_window = self.config.get_context_window(provider, model)
                        except Exception:
                            ctx_window = 8192
                        budget_chars = int(ctx_window * 0.4 * 4) // max(len(tool_calls_this_round), 1)
                        budget_chars = max(budget_chars, 2000)
                        llm_result = self._truncate_tool_result(result, max_chars=budget_chars)
                        api_messages.append({
                            'role': 'tool',
                            'tool_call_id': tc.get('id', ''),
                            'content': json.dumps(llm_result)
                        })
            else:
                self.call_from_thread(
                    self._append_status, "Synthesizing final response..."
                )
                try:
                    for chunk in stream_func(api_messages, merged_cfg, tools=None):
                        if isinstance(chunk, dict) and chunk.get('type') == 'usage':
                            stream_usage = chunk
                        elif isinstance(chunk, str):
                            full_response += chunk
                            self.call_from_thread(self._append_streaming, chunk)
                except Exception as synth_err:
                    print(f"Synthesis call failed: {synth_err}")
                    if not full_response:
                        full_response = "\n\n".join(working_texts) if working_texts else "(Tool results above)"
        except LLMApiError as api_err:
            cls = classify_error(api_err.status_code, api_err.response_text)
            msg = friendly_message(cls, api_err.provider, api_err.status_code, api_err.response_text)
            self.call_from_thread(self._append_error, msg)
        except Exception as e:
            self.call_from_thread(self._append_error, str(e))
        finally:
            self._streaming = False

        if full_response and not self.temporary_mode and not self.incognito_mode:
            self.chat_manager.add_message("assistant", full_response, model)
            msgs = self.chat_manager.current_chat.get("chat_history", [])
            if msgs:
                if tool_events_log:
                    msgs[-1]["tool_events"] = tool_events_log
                if working_texts:
                    msgs[-1]["workingText"] = "\n\n".join(working_texts)
                if stream_usage:
                    msgs[-1]["usage"] = {
                        "input_tokens": stream_usage.get("input_tokens", 0),
                        "output_tokens": stream_usage.get("output_tokens", 0),
                    }
                    msg_cost = calc_api_cost(
                        provider, model,
                        stream_usage.get("input_tokens", 0),
                        stream_usage.get("output_tokens", 0)
                    )
                    msgs[-1]["cost"] = msg_cost
                    msgs[-1]["provider"] = provider
                self.chat_manager.save_current_chat()

            self.call_from_thread(self._maybe_generate_title, provider)
            self.call_from_thread(self._render_final_response, full_response)

            if stream_usage:
                input_t = stream_usage.get("input_tokens", 0)
                output_t = stream_usage.get("output_tokens", 0)
                cost = calc_api_cost(provider, model, input_t, output_t)
                self.call_from_thread(
                    self._append_status,
                    f"Tokens: {input_t} in / {output_t} out | Cost: ${cost:.4f}"
                )
        elif full_response and self.incognito_mode:
            self.call_from_thread(self._render_final_response, full_response)

    def _render_final_response(self, content: str):
        try:
            cd = self.screen.query_one("#chat-display", ChatDisplay)
            cd.render_final_response(content)
        except Exception:
            pass

    def _append_streaming(self, text: str):
        try:
            cd = self.screen.query_one("#chat-display", ChatDisplay)
            cd._streaming_buffer += text
            cd._update_streaming_display()
        except Exception:
            pass

    def _append_error(self, msg: str):
        try:
            cd = self.screen.query_one("#chat-display", ChatDisplay)
            cd.mount(Static(f"\n[bold red]Error:[/] {msg}", markup=True))
        except Exception:
            pass

    def _append_status(self, msg: str):
        try:
            cd = self.screen.query_one("#chat-display", ChatDisplay)
            cd.show_status(msg)
        except Exception:
            pass

    def _append_tool_event(self, name: str, args: dict):
        try:
            cd = self.screen.query_one("#chat-display", ChatDisplay)
            cd.show_tool_start(name, args)
        except Exception:
            pass

    def _append_tool_result(self, name: str, result: dict):
        try:
            cd = self.screen.query_one("#chat-display", ChatDisplay)
            cd.show_tool_end(name, result)
        except Exception:
            pass

    def _maybe_generate_title(self, provider: str):
        try:
            chat_hist = self.chat_manager.current_chat.get("chat_history", [])
            current_title = self.chat_manager.current_chat.get("title", "")
            current_file = self.chat_manager.current_chat_file

            is_prompt_branch = False
            if current_file:
                is_prompt_branch = self.folder_manager.is_prompt_branch(current_file)

            if (
                not self.temporary_mode
                and not self.incognito_mode
                and (current_title == "New Chat" or current_title == "")
                and len(chat_hist) == 2
                and not is_prompt_branch
            ):
                title_provider = self.config.get("title_provider", "groq")
                title_model = self.config.get("title_model", "llama-3.1-8b-instant")

                title_stream = self._get_stream_func(title_provider)
                if not title_stream:
                    return

                user_text = chat_hist[0].get("content", "")[:500]
                asst_text = chat_hist[1].get("content", "")[:500]
                title_prompt = (
                    "Generate a short title (under 60 characters, no quotes) "
                    "summarizing this conversation.\n"
                    f"User: {user_text}\n"
                    f"Assistant: {asst_text}\n"
                    "Title:"
                )

                title_cfg = dict(self.config.config)
                api_key = self.config.get_api_key(title_provider)
                if api_key:
                    title_cfg[f"{title_provider}_api_key"] = api_key
                if title_provider == "llamacpp":
                    title_cfg["llamacpp_url"] = self._get_llamacpp_url()
                self._inject_endpoint_config(title_provider, title_cfg)
                title_cfg.update({
                    "model": title_model,
                    f"{title_provider}_model": title_model,
                    f"{title_provider}_temperature": 0.3,
                    f"{title_provider}_max_tokens": 60,
                    f"{title_provider}_system_prompt": "",
                    "temperature": 0.3,
                    "system_prompt": "",
                    "max_tokens": 60,
                })

                generated = ""
                for chunk in title_stream(
                    [{"role": "user", "content": title_prompt}],
                    title_cfg,
                ):
                    if isinstance(chunk, str):
                        generated += chunk

                generated = generated.strip().strip('"').strip("'").strip()
                if generated:
                    generated = generated[:60]
                    self._rename_chat_file(generated)
        except Exception as title_err:
            print(f"Auto-title generation failed: {title_err}")

    def _rename_chat_file(self, new_title: str):
        try:
            filename = self.chat_manager.current_chat_file
            if not filename:
                return

            safe = re.sub(r"[^\w\s-]", "", new_title)
            safe = re.sub(r"[-\s]+", "_", safe).strip("_")
            base = os.path.splitext(filename)[0]
            m = re.search(r"_(\d{8}_\d{6})$", base)
            ts = m.group(1) if m else datetime.now().strftime("%Y%m%d_%H%M%S")
            new_fn = f"{safe}_{ts}.json"

            old_path = os.path.join(self.chat_manager.chats_directory, filename)
            new_path = os.path.join(self.chat_manager.chats_directory, new_fn)

            if os.path.exists(old_path):
                with open(old_path, "r", encoding="utf-8") as f:
                    chat_data = json.load(f)

                if isinstance(chat_data, dict):
                    chat_data["title"] = new_title
                    if "chat_id" not in chat_data:
                        chat_data["chat_id"] = str(uuid.uuid4())
                    if "root_chat_id" not in chat_data:
                        chat_data["root_chat_id"] = chat_data["chat_id"]
                    if "parent_chat_id" not in chat_data:
                        chat_data["parent_chat_id"] = ""

                with open(new_path, "w", encoding="utf-8") as f:
                    json.dump(chat_data, f, indent=2, ensure_ascii=False)
                os.remove(old_path)

                self.chat_manager.current_chat_file = new_fn
                if isinstance(self.chat_manager.current_chat, dict):
                    self.chat_manager.current_chat["title"] = new_title

                folder_id = self.folder_manager.get_chat_folder(filename)
                if folder_id:
                    self.folder_manager.remove_chat_from_folder(filename)
                    self.folder_manager.assign_chat_to_folder(new_fn, folder_id)

                try:
                    header = self.screen.query_one("#header", HeaderBar)
                    header.update_header()
                except Exception:
                    pass
        except Exception as e:
            print(f"Failed to rename chat: {e}")

    def _truncate_tool_result(self, result: dict, max_chars: int = 3000) -> dict:
        truncated = dict(result)
        text_fields = ['stdout', 'stderr', 'content', 'data']
        for field in text_fields:
            if field in truncated and isinstance(truncated[field], str):
                val = truncated[field]
                if len(val) > max_chars:
                    half = max_chars // 2
                    head = val[:half]
                    tail = val[-half:]
                    nl = head.rfind('\n')
                    if nl > half // 2:
                        head = head[:nl]
                    nl = tail.find('\n')
                    if nl != -1 and nl < half // 2:
                        tail = tail[nl + 1:]
                    omitted = len(val) - len(head) - len(tail)
                    total_lines = val.count('\n') + 1
                    truncated[field] = f"{head}\n\n[... {omitted} chars omitted, {total_lines} total lines ...]\n\n{tail}"
        if 'result' in truncated and isinstance(truncated['result'], dict):
            truncated['result'] = self._truncate_tool_result(truncated['result'], max_chars)
        return truncated


class HeaderBar(Horizontal):
    DEFAULT_CSS = """
    HeaderBar {
        height: 3;
        background: #16213e;
        padding: 0 1;
    }
    """

    MODEL_DISPLAY_MAX = 28

    def compose(self) -> ComposeResult:
        tb_app = self.app
        provider = tb_app.config.get("provider", "unknown")
        model = tb_app.config.get(f"{provider}_model", "(not set)")
        chat = tb_app.chat_manager.current_chat
        title = chat.get("title", "New Chat") if chat else "New Chat"

        provider_options = self._build_provider_options()
        model_options = self._build_model_options(provider)
        prompt_options = self._build_prompt_options()

        yield Static(f" ThreadBear v1.0 | {title}", id="header-title")
        yield Select(provider_options, value=provider, id="provider-select", allow_blank=False)
        yield Select(model_options, value=model if model_options else Select.NULL, id="model-select", allow_blank=not model_options)
        yield Select(prompt_options, value="(default)", id="prompt-select", allow_blank=False)

    def _truncate(self, text: str, max_len: int = 0) -> str:
        if max_len <= 0:
            max_len = self.MODEL_DISPLAY_MAX
        if len(text) > max_len:
            return text[:max_len - 3] + "..."
        return text

    def _build_provider_options(self):
        tb_app = self.app
        options = []
        for p in tb_app.available_providers:
            options.append((p.capitalize(), p))
        return options

    def _build_model_options(self, provider):
        tb_app = self.app
        models = tb_app.config.get_models_for_provider(provider)
        current = tb_app.config.get(f"{provider}_model", "")
        options = []
        seen = set()
        for m in models:
            if m and m not in seen:
                seen.add(m)
                display = self._truncate(m)
                options.append((display, m))
        if current and current not in seen:
            display = self._truncate(current)
            options.insert(0, (display, current))
        if not options and current:
            display = self._truncate(current)
            options.append((display, current))
        return options

    def _build_prompt_options(self):
        tb_app = self.app
        prompts_dir = tb_app.project_root / "prompts"
        options = [("(default)", "(default)")]
        for fname in ["default_prompts.jsonl", "custom_prompts.jsonl"]:
            fpath = prompts_dir / fname
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entry = json.loads(line)
                                name = entry.get("name", entry.get("id", "unnamed"))
                                options.append((name, name))
                            except json.JSONDecodeError:
                                pass
        return options

    @on(Select.Changed, "#provider-select")
    def on_provider_changed(self, event: Select.Changed):
        if event.value is not Select.NULL:
            tb_app = self.app
            tb_app.config.set("provider", event.value)
            tb_app.config.save_config()
            self._refresh_model_select(event.value)
            self._refresh_header_title()
            self._sync_settings_provider(event.value)

    @on(Select.Changed, "#model-select")
    def on_model_changed(self, event: Select.Changed):
        if event.value is not Select.NULL:
            tb_app = self.app
            provider = tb_app.config.get("provider", "unknown")
            tb_app.config.set(f"{provider}_model", event.value)
            tb_app.config.add_recent_model(provider, event.value)
            tb_app.config.save_config()
            self._sync_settings_model(event.value)

    @on(Select.Changed, "#prompt-select")
    def on_prompt_changed(self, event: Select.Changed):
        if event.value is not Select.NULL and event.value != "(default)":
            tb_app = self.app
            provider = tb_app.config.get("provider", "unknown")
            model = tb_app.config.get(f"{provider}_model", "")
            prompt_name = event.value
            self._load_prompt_content(prompt_name, provider, model)

    def _refresh_model_select(self, provider):
        try:
            model_select = self.query_one("#model-select", Select)
            options = self._build_model_options(provider)
            current_model = self.app.config.get(f"{provider}_model", "")
            model_select.allow_blank = not bool(options)
            if options:
                model_select.set_options(options)
                if current_model:
                    model_select.value = current_model
            else:
                model_select.value = Select.NULL
        except Exception:
            pass

    def _load_prompt_content(self, prompt_name, provider, model):
        prompts_dir = self.app.project_root / "prompts"
        for fname in ["default_prompts.jsonl", "custom_prompts.jsonl"]:
            fpath = prompts_dir / fname
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entry = json.loads(line)
                                if entry.get("name") == prompt_name or entry.get("id") == prompt_name:
                                    content = entry.get("content", "")
                                    if content:
                                        self.app.config.set(f"{provider}_system_prompt", content)
                                        self.app.config.save_config()
                                    return
                            except json.JSONDecodeError:
                                pass

    def _refresh_header_title(self):
        try:
            title_widget = self.query_one("#header-title", Static)
            chat = self.app.chat_manager.current_chat
            title = chat.get("title", "New Chat") if chat else "New Chat"
            title_widget.update(f" ThreadBear v1.0 | {title}")
        except Exception:
            pass

    def update_header(self):
        self._refresh_header_title()
        provider = self.app.config.get("provider", "unknown")
        self._refresh_model_select(provider)

    def _sync_settings_provider(self, provider):
        try:
            settings = self.app.screen.query_one("#right-sidebar", SettingsPanel)
            settings.query_one("#settings-provider-select", Select).value = provider
            settings._refresh_settings_model_select(provider)
        except Exception:
            pass

    def _sync_settings_model(self, model):
        try:
            settings = self.app.screen.query_one("#right-sidebar", SettingsPanel)
            settings.query_one("#settings-model-select", Select).value = model
        except Exception:
            pass

    def refresh_models_from_api(self, provider):
        models = self._fetch_models_from_api(provider)
        if models:
            self.app.config.update_stored_models(provider, models)
            current = self.app.config.get(f"{provider}_model", "")
            if current not in models:
                self.app.config.set(f"{provider}_model", models[0])
            self.app.config.save_config()
            self.call_from_thread(self._on_models_fetched, provider)

    def _on_models_fetched(self, provider):
        self._refresh_model_select(provider)
        try:
            settings = self.app.screen.query_one("#right-sidebar", SettingsPanel)
            settings.refresh_after_model_fetch(provider)
        except Exception:
            pass

    def _fetch_models_from_api(self, provider):
        try:
            if provider == "llamacpp":
                return self._fetch_llamacpp_models()
            elif provider in self.app.config.get("custom_endpoints", {}):
                return self._fetch_custom_endpoint_models(provider)
            elif provider == "groq":
                return self._fetch_groq_models()
            elif provider == "google":
                return self._fetch_google_models()
            elif provider == "mistral":
                return self._fetch_mistral_models()
            elif provider == "openrouter":
                return self._fetch_openrouter_models()
        except Exception as e:
            print(f"Failed to fetch models for {provider}: {e}")
        return []

    def _fetch_groq_models(self):
        api_key = self.app.config.get_api_key("groq")
        if not api_key:
            return []
        resp = requests.get("https://api.groq.com/openai/v1/models",
                          headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [m["id"] for m in sorted(data, key=lambda x: x.get("id", ""))]

    def _fetch_google_models(self):
        api_key = self.app.config.get_api_key("google")
        if not api_key:
            return []
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key, "pageSize": 100}, timeout=30
        )
        resp.raise_for_status()
        data = resp.json().get("models", [])
        models = []
        for m in data:
            name = m.get("name", "").replace("models/", "")
            if name and "embedding" not in name.lower():
                models.append(name)
        return sorted(models)

    def _fetch_mistral_models(self):
        api_key = self.app.config.get_api_key("mistral")
        if not api_key:
            return []
        resp = requests.get("https://api.mistral.ai/v1/models",
                          headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        return [m["id"] for m in sorted(data, key=lambda x: x.get("id", ""))]

    def _fetch_openrouter_models(self):
        try:
            resp = requests.get("https://openrouter.ai/api/v1/models", timeout=30)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [m["id"] for m in sorted(data, key=lambda x: x.get("id", ""))]
        except Exception:
            return []

    def _fetch_llamacpp_models(self):
        url = self.app.config.get("llamacpp_url", "http://localhost:8080")
        try:
            resp = requests.get(f"{url.rstrip('/')}/v1/models", timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [m.get("id", "") for m in data if m.get("id")]
        except Exception:
            return []

    def _fetch_custom_endpoint_models(self, provider):
        endpoints = self.app.config.get("custom_endpoints", {})
        if provider not in endpoints:
            return []
        ep = endpoints[provider]
        base_url = ep.get("base_url", "").rstrip("/")
        api_key = self.app.config.get_api_key(provider)
        catalog = fetch_openai_compat_catalog(base_url, api_key)
        return [m["id"] for m in catalog if m.get("id")]


class Sidebar(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("FOLDERS", classes="sidebar-section-title")
        yield Tree("root", id="folder-tree")
        yield Static("\u2500" * 28, classes="sidebar-item")
        yield Static("CHATS", classes="sidebar-section-title")
        yield Tree("root", id="chat-list")

    def on_mount(self):
        self._build_folder_tree()
        self._build_chat_list()

    def _build_folder_tree(self):
        tree = self.query_one("#folder-tree", Tree)
        tree.clear()
        tb_app = self.app
        folder_tree = tb_app.folder_manager.get_folder_tree()

        for folder in folder_tree:
            chat_count = len(tb_app.folder_manager.get_folder_contents(folder["id"])["chats"])
            label = f"\U0001f4c1 {folder['name']} ({chat_count})"
            node = tree.root.add(label, data={"type": "folder", "id": folder["id"], "name": folder["name"]})
            if folder["children"]:
                for child in folder["children"]:
                    child_count = len(tb_app.folder_manager.get_folder_contents(child["id"])["chats"])
                    node.add(f"  \U0001f4c2 {child['name']} ({child_count})", data={"type": "folder", "id": child["id"], "name": child["name"]})
        tree.refresh()

    def _build_chat_list(self):
        tree = self.query_one("#chat-list", Tree)
        tree.clear()
        tb_app = self.app
        all_chats = tb_app.chat_manager.get_chat_list()
        all_chats.sort(key=lambda c: c.get("modified", ""), reverse=True)
        active_file = tb_app.chat_manager.current_chat_file

        for chat in all_chats[:30]:
            fn = chat["filename"]
            title = chat.get("title", fn.replace(".json", "").replace("_", " "))
            is_active = (fn == active_file)
            prefix = "\u25cf" if is_active else "\u25cb"
            label = f"  {prefix} {title}"
            tree.root.add(label, data={"type": "chat", "filename": fn, "title": title})
        tree.refresh()

    @on(Tree.NodeSelected, "#folder-tree")
    def on_folder_selected(self, event: Tree.NodeSelected):
        data = event.node.data
        if data and data.get("type") == "folder":
            self._show_folder_contents(data["id"], data["name"])

    def _show_folder_contents(self, folder_id, folder_name):
        chat_display = self.app.screen.query_one("#chat-display", ChatDisplay)
        for child in list(chat_display.children):
            child.remove()
        contents = self.app.folder_manager.get_folder_contents(folder_id)
        chats = contents.get("chats", [])
        if not chats:
            chat_display.mount(Static(f"\n[dim]Folder: {folder_name} (no chats)[/dim]", markup=True))
            return
        lines = [f"\n[bold]{folder_name}[/bold]\n"]
        for fn in chats:
            path = os.path.join(self.app.chat_manager.chats_directory, fn)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                title = data.get("title", fn.replace(".json", "").replace("_", " "))
                lines.append(f"  \u25cb {title} ({fn})")
        chat_display.mount(Static("\n".join(lines), markup=True))

    @on(Tree.NodeSelected, "#chat-list")
    def on_chat_selected(self, event: Tree.NodeSelected):
        data = event.node.data
        if data and data.get("type") == "chat":
            self._load_chat(data["filename"])

    def _load_chat(self, filename):
        tb_app = self.app
        success = tb_app.chat_manager.load_chat(filename)
        if success:
            chat_display = self.app.screen.query_one("#chat-display", ChatDisplay)
            chat_display._refresh_chat()
            try:
                header = self.app.screen.query_one("#header", HeaderBar)
                header.update_header()
            except Exception:
                pass
            self._build_chat_list()
        else:
            chat_display = self.app.screen.query_one("#chat-display", ChatDisplay)
            chat_display.mount(Static(f"\n[bold red]Error loading chat: {filename}[/bold red]", markup=True))


class ChatDisplay(VerticalScroll):
    @staticmethod
    def _normalize_role(raw_role: str) -> str:
        role = (raw_role or "").strip().lower()
        if role in {"assistant", "ai", "bot", "model"}:
            return "assistant"
        if role in {"user", "human"}:
            return "user"
        if role in {"system"}:
            return "system"
        if role in {"tool", "function"}:
            return "tool"
        return role

    @staticmethod
    def _extract_text_content(content) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, (int, float, bool)):
            return str(content)
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if "text" in item and isinstance(item["text"], str):
                        parts.append(item["text"])
                    elif item.get("type") == "text":
                        txt = item.get("content") or item.get("value")
                        if isinstance(txt, str):
                            parts.append(txt)
            return "\n".join(p for p in parts if p).strip()
        if isinstance(content, dict):
            for key in ("text", "content", "message", "response", "output"):
                val = content.get(key)
                if isinstance(val, str):
                    return val
            try:
                return json.dumps(content, ensure_ascii=False, indent=2)
            except Exception:
                return str(content)
        return str(content)

    def on_mount(self):
        self._streaming_buffer = ""
        self._streaming_widget = None
        self._status_widget = None
        self._refresh_chat()

    def _refresh_chat(self):
        for child in list(self.children):
            child.remove()
        tb_app = self.app
        chat = tb_app.chat_manager.current_chat
        if chat and chat.get("chat_history"):
            for msg in chat["chat_history"]:
                role = self._normalize_role(msg.get("role", "unknown"))
                content = self._extract_text_content(msg.get("content", ""))
                if role == "system":
                    self.mount(Static(f"[dim]{content[:100]}...[/dim]", markup=True))
                    continue
                if role == "user":
                    container = Vertical(classes="message-turn")
                    self.mount(container)
                    container.mount(Static("You", classes="user-label"))
                    bubble = Vertical(classes="message-bubble-user")
                    bubble.mount(Static(content, classes="user-content"))
                    container.mount(bubble)
                elif role == "assistant":
                    provider = tb_app.config.get("provider", "unknown")
                    model = tb_app.config.get(f"{provider}_model", "unknown")
                    label = f"{provider}/{model}"
                    container = Vertical(classes="message-turn")
                    self.mount(container)
                    container.mount(Static(label, classes="assistant-label"))
                    bubble = Vertical(classes="message-bubble-assistant")
                    bubble.mount(Markdown(content if content else "_(empty message)_", classes="assistant-content"))
                    container.mount(bubble)
                elif role == "tool":
                    # Keep tool messages from creating blank rows in history rendering.
                    continue
                else:
                    # Legacy/unknown role: render as plain message instead of blank separators.
                    container = Vertical(classes="message-turn")
                    self.mount(container)
                    container.mount(Static(role or "message", classes="assistant-label"))
                    bubble = Vertical(classes="message-bubble-assistant")
                    bubble.mount(Static(content or "[dim](empty)[/dim]", markup=True, classes="assistant-content"))
                    container.mount(bubble)
        else:
            self.mount(Static("[dim]ThreadBear v1.0.0[/dim]\n[dim]Type /help for commands, /quit to exit[/dim]\n[dim]Features: branching, tools, docs, folders, endpoints[/dim]", markup=True))
        self.scroll_end()

    def show_thinking(self):
        if self._status_widget:
            return
        self._status_widget = Static("[dim italic]Thinking...[/dim italic]", markup=True)
        self.mount(self._status_widget)
        self.scroll_end()

    def hide_thinking(self):
        if self._status_widget:
            self._status_widget.remove()
            self._status_widget = None

    def _get_model_label(self):
        tb_app = self.app
        provider = tb_app.config.get("provider", "unknown")
        model = tb_app.config.get(f"{provider}_model", "unknown")
        return f"{provider}/{model}"

    def _update_streaming_display(self):
        self.hide_thinking()
        label = self._get_model_label()
        full_text = f"[bold #87d787]{label}[/bold #87d787]\n{self._streaming_buffer}"
        if self._streaming_widget:
            self._streaming_widget.update(full_text, markup=True)
        else:
            self._streaming_widget = Static(full_text, markup=True)
            self.mount(self._streaming_widget)
        self.scroll_end()

    def append_message(self, role: str, content: str):
        self.hide_thinking()
        self._streaming_buffer = ""
        self._streaming_widget = None
        tb_app = self.app
        container = Vertical(classes="message-turn")
        if role == "user":
            container.mount(Static("You", classes="user-label"))
            bubble = Vertical(classes="message-bubble-user")
            bubble.mount(Static(content, classes="user-content"))
            container.mount(bubble)
        elif role == "assistant":
            label = self._get_model_label()
            container.mount(Static(label, classes="assistant-label"))
            bubble = Vertical(classes="message-bubble-assistant")
            bubble.mount(Markdown(content if content else "_(empty message)_", classes="assistant-content"))
            container.mount(bubble)
        self.mount(container)
        self.scroll_end()

    def render_final_response(self, content: str):
        self.hide_thinking()
        if self._streaming_widget:
            self._streaming_widget.remove()
        self._streaming_buffer = ""
        self._streaming_widget = None
        label = self._get_model_label()
        container = Vertical(classes="message-turn")
        container.mount(Static(label, classes="assistant-label"))
        bubble = Vertical(classes="message-bubble-assistant")
        bubble.mount(Markdown(content if content else "_(empty message)_", classes="assistant-content"))
        container.mount(bubble)
        self.mount(container)
        self.scroll_end()

    def show_status(self, msg: str):
        if self._status_widget:
            self._status_widget.update(f"[dim italic]{msg}[/dim italic]", markup=True)
        else:
            self._status_widget = Static(f"[dim italic]{msg}[/dim italic]", markup=True)
            self.mount(self._status_widget)
            self.scroll_end()

    def show_tool_start(self, name: str, args: dict):
        args_preview = json.dumps(args)[:100] if args else ""
        self.mount(Static(f"\n[bold #ffaa00]Tool: {name}[/bold #ffaa00]\n[dim]Args: {args_preview}[/dim]", markup=True))
        self.scroll_end()

    def show_tool_end(self, name: str, result: dict):
        success = result.get("success", False)
        status = "[bold #87d787]OK[/bold #87d787]" if success else "[bold red]FAIL[/bold red]"
        self.mount(Static(f"\n{status} [dim]Tool {name} completed[/dim]", markup=True))
        self.scroll_end()


class SettingsPanel(VerticalScroll):
    def compose(self) -> ComposeResult:
        tb_app = self.app
        provider = tb_app.config.get("provider", "unknown")
        model = tb_app.config.get(f"{provider}_model", "(not set)")
        temp = tb_app.config.get(f"{provider}_temperature", 0.7)
        max_tok = tb_app.config.get(f"{provider}_max_tokens", 4096)
        top_p = tb_app.config.get(f"{provider}_top_p", 1.0)
        ctx = tb_app.config.get_context_window(provider, model)

        yield Static("[bold]Settings[/bold]", markup=True)
        yield Static("\u2500" * 32)

        yield Static("  Provider", classes="sidebar-section-title")
        yield Select(self._provider_options(), value=provider, id="settings-provider-select", allow_blank=False)

        yield Static("  Model", classes="sidebar-section-title")
        _settings_model_opts = self._model_options(provider)
        yield Select(_settings_model_opts, value=model if _settings_model_opts else Select.NULL, id="settings-model-select", allow_blank=not _settings_model_opts)

        yield Static("  Temperature", classes="sidebar-section-title")
        yield Input(value=str(temp), id="settings-temp-input", placeholder="0.7")

        yield Static("  Max Tokens", classes="sidebar-section-title")
        yield Input(value=str(max_tok), id="settings-max-tokens-input", placeholder="4096")

        yield Static("  Top P", classes="sidebar-section-title")
        yield Input(value=str(top_p), id="settings-top-p-input", placeholder="1.0")

        yield Static("  Context Window", classes="sidebar-section-title")
        yield Input(value=str(ctx), id="settings-context-input", placeholder="8192")

        yield Static("\u2500" * 32)
        yield Static("[bold]Actions[/bold]", markup=True)
        yield Static("  [\u21bb] Refresh models from API", classes="sidebar-item", id="refresh-models-btn")

    def _provider_options(self):
        tb_app = self.app
        options = []
        for p in tb_app.available_providers:
            options.append((p.capitalize(), p))
        return options

    def _model_options(self, provider):
        tb_app = self.app
        models = tb_app.config.get_models_for_provider(provider)
        current = tb_app.config.get(f"{provider}_model", "")
        options = []
        seen = set()
        for m in models:
            if m and m not in seen:
                seen.add(m)
                display = m if len(m) <= 28 else m[:25] + "..."
                options.append((display, m))
        if current and current not in seen:
            display = current if len(current) <= 28 else current[:25] + "..."
            options.insert(0, (display, current))
        if not options and current:
            display = current if len(current) <= 28 else current[:25] + "..."
            options.append((display, current))
        return options

    @on(Select.Changed, "#settings-provider-select")
    def on_settings_provider_changed(self, event: Select.Changed):
        if event.value is not Select.NULL:
            tb_app = self.app
            tb_app.config.set("provider", event.value)
            tb_app.config.save_config()
            self._refresh_settings_model_select(event.value)
            self._sync_header_provider(event.value)

    @on(Select.Changed, "#settings-model-select")
    def on_settings_model_changed(self, event: Select.Changed):
        if event.value is not Select.NULL:
            tb_app = self.app
            provider = tb_app.config.get("provider", "unknown")
            tb_app.config.set(f"{provider}_model", event.value)
            tb_app.config.add_recent_model(provider, event.value)
            tb_app.config.save_config()
            self._sync_header_model(event.value)

    @on(Input.Submitted, "#settings-temp-input")
    def on_temp_submitted(self, event: Input.Submitted):
        try:
            val = float(event.value)
            tb_app = self.app
            provider = tb_app.config.get("provider", "unknown")
            tb_app.config.set(f"{provider}_temperature", val)
            tb_app.config.save_config()
        except ValueError:
            pass

    @on(Input.Submitted, "#settings-max-tokens-input")
    def on_max_tokens_submitted(self, event: Input.Submitted):
        try:
            val = int(event.value)
            if val > 0:
                tb_app = self.app
                provider = tb_app.config.get("provider", "unknown")
                tb_app.config.set(f"{provider}_max_tokens", val)
                tb_app.config.save_config()
        except ValueError:
            pass

    @on(Input.Submitted, "#settings-top-p-input")
    def on_top_p_submitted(self, event: Input.Submitted):
        try:
            val = float(event.value)
            if 0 <= val <= 1:
                tb_app = self.app
                provider = tb_app.config.get("provider", "unknown")
                tb_app.config.set(f"{provider}_top_p", val)
                tb_app.config.save_config()
        except ValueError:
            pass

    @on(Input.Submitted, "#settings-context-input")
    def on_context_submitted(self, event: Input.Submitted):
        try:
            val = int(event.value)
            if val > 0:
                tb_app = self.app
                provider = tb_app.config.get("provider", "unknown")
                model = tb_app.config.get(f"{provider}_model", "")
                tb_app.config.set_model_settings(provider, model, {"context_window": val})
        except ValueError:
            pass

    @on(Click, "#refresh-models-btn")
    def on_refresh_models(self, event: Click):
        provider = self.app.config.get("provider", "unknown")
        header = self.app.screen.query_one("#header", HeaderBar)
        header.refresh_models_from_api(provider)

    def _refresh_settings_model_select(self, provider):
        try:
            model_select = self.query_one("#settings-model-select", Select)
            options = self._model_options(provider)
            current_model = self.app.config.get(f"{provider}_model", "")
            model_select.allow_blank = not bool(options)
            if options:
                model_select.set_options(options)
                if current_model:
                    model_select.value = current_model
            else:
                model_select.value = Select.NULL
        except Exception:
            pass

    def _sync_header_provider(self, provider):
        try:
            header = self.app.screen.query_one("#header", HeaderBar)
            header.query_one("#provider-select", Select).value = provider
            header._refresh_model_select(provider)
        except Exception:
            pass

    def _sync_header_model(self, model):
        try:
            header = self.app.screen.query_one("#header", HeaderBar)
            header.query_one("#model-select", Select).value = model
        except Exception:
            pass

    def refresh_after_model_fetch(self, provider):
        self._refresh_settings_model_select(provider)
        try:
            header = self.app.screen.query_one("#header", HeaderBar)
            header._refresh_model_select(provider)
        except Exception:
            pass


class FooterBar(Static):
    def on_mount(self):
        self.update(" Ctrl+L:Sidebar  Ctrl+R:Settings  Ctrl+D:Quit  Esc:Focus  /help:Commands ")


class MainScreen(Screen):
    def compose(self) -> ComposeResult:
        yield HeaderBar(id="header")
        with Container(id="main-layout"):
            yield Sidebar(id="left-sidebar")
            with Vertical(id="center-panel"):
                yield ChatDisplay(id="chat-display")
                yield Horizontal(Input(placeholder="Type a message or /command...", id="chat-input"), id="input-bar")
            yield SettingsPanel(id="right-sidebar")
        yield FooterBar(id="footer")

    def on_mount(self):
        self.set_timer(0.5, self._focus_input)

    def _focus_input(self):
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

    @on(Input.Submitted, "#chat-input")
    def on_input_submitted(self, event: Input.Submitted):
        text = event.value
        input_widget = self.query_one("#chat-input", Input)
        input_widget.value = ""

        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._send_message(text)

    def _handle_command(self, text: str):
        try:
            tokens = shlex.split(text[1:].strip())
        except ValueError as parse_err:
            self.query_one("#chat-display", ChatDisplay).mount(
                Static(f"\n[bold red]Invalid command syntax: {parse_err}[/bold red]", markup=True)
            )
            return

        if not tokens:
            return

        cmd = tokens[0].lower()
        args = tokens[1:]
        chat_display = self.query_one("#chat-display", ChatDisplay)

        if cmd in ("quit", "exit"):
            self.app.config.save_config()
            self.app.exit()
        elif cmd == "help":
            chat_display.mount(Static(
                "\n[bold]Commands:[/bold]\n"
                "  /new              - Start a new chat\n"
                "  /quit             - Exit ThreadBear\n"
                "  /help             - Show this help\n"
                "  /clear            - Clear chat display\n"
                "  /settings         - Show current settings\n"
                "  /list             - Refresh sidebar\n"
                "  /provider         - Show available providers\n"
                "  /model            - Show current model\n"
                "  /rename <title>   - Rename current chat\n"
                "  /delete <idx|last> - Delete a message\n"
                "  /branch [name]    - Fork from last response\n"
                "  /branches         - List branches\n"
                "  /temporary        - Toggle temp mode\n"
                "  /incognito        - Toggle incognito\n"
                "  /cancel           - Cancel generation\n"
                "  /context          - Show token usage\n"
                "  /docs <subcmd>    - Manage documents\n"
                "  /folders <subcmd> - Manage folders\n"
                "  /tools <subcmd>   - Tool configuration\n"
                "  /toolbox <subcmd> - Toolbox scripts\n"
                "  /toolbelt <subcmd>- Per-chat scripts\n"
                "  /endpoints <sub>  - Custom endpoints\n"
                "  /prompts          - System prompts",
                markup=True,
            ))
        elif cmd == "new":
            self.app.chat_manager.create_new_chat()
            chat_display._refresh_chat()
            try:
                sidebar = self.query_one("#left-sidebar", Sidebar)
                sidebar._build_chat_list()
            except Exception:
                pass
            try:
                header = self.query_one("#header", HeaderBar)
                header.update_header()
            except Exception:
                pass
        elif cmd == "clear":
            chat_display._refresh_chat()
        elif cmd == "settings":
            provider = self.app.config.get("provider")
            model = self.app.config.get(f"{provider}_model", "(not set)")
            temp = self.app.config.get(f"{provider}_temperature", 0.7)
            chat_display.mount(Static(
                f"\nProvider: {provider}\nModel: {model}\nTemp: {temp}",
                markup=True,
            ))
        elif cmd == "list":
            try:
                sidebar = self.query_one("#left-sidebar", Sidebar)
                sidebar._build_folder_tree()
                sidebar._build_chat_list()
            except Exception:
                pass
        elif cmd == "provider":
            providers = self.app.available_providers
            current = self.app.config.get("provider", "unknown")
            lines = [f"\n[bold]Available providers:[/bold]"]
            for p in providers:
                marker = "\u25cf" if p == current else "\u25cb"
                lines.append(f"  {marker} {p}")
            chat_display.mount(Static("\n".join(lines), markup=True))
        elif cmd == "model":
            provider = self.app.config.get("provider", "unknown")
            model = self.app.config.get(f"{provider}_model", "(not set)")
            chat_display.mount(Static(f"\nCurrent model: {model} (provider: {provider})", markup=True))

        elif cmd == "rename":
            new_title = " ".join(args).strip()
            if not new_title:
                chat_display.mount(Static("\n[bold red]Usage: /rename <new_title>[/bold red]", markup=True))
            else:
                self.app._rename_chat_file(new_title)
                try:
                    sidebar = self.query_one("#left-sidebar", Sidebar)
                    sidebar._build_chat_list()
                except Exception:
                    pass
                chat_display.mount(Static(f"\n[dim]Chat renamed to: {new_title}[/dim]", markup=True))

        elif cmd == "delete":
            arg = (args[0] if args else "").strip()
            msgs = self.app.chat_manager.current_chat.get("chat_history", [])
            if arg.lower() == "last":
                idx = len(msgs) - 1
            else:
                try:
                    idx = int(arg)
                except ValueError:
                    chat_display.mount(Static("\n[bold red]Usage: /delete <index|last>[/bold red]", markup=True))
                    return
            if 0 <= idx < len(msgs):
                msgs.pop(idx)
                self.app.chat_manager.current_chat["chat_history"] = msgs
                self.app.chat_manager.save_current_chat(force_save=True)
                chat_display._refresh_chat()
                chat_display.mount(Static(f"\n[dim]Deleted message at index {idx}[/dim]", markup=True))
            else:
                chat_display.mount(Static(f"\n[bold red]Invalid index: {idx}[/bold red]", markup=True))

        elif cmd == "branch":
            name = " ".join(args).strip()
            msgs = self.app.chat_manager.current_chat.get("chat_history", [])
            branch_idx = -1
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "assistant":
                    branch_idx = i
                    break
            if branch_idx < 0:
                chat_display.mount(Static("\n[bold red]No assistant message to branch from[/bold red]", markup=True))
                return

            parent_chat_id = self.app.chat_manager.current_chat.get("chat_id", self.app.chat_manager.current_chat_file)
            root_chat_id = self.app.chat_manager.current_chat.get("root_chat_id", parent_chat_id)

            try:
                fork = self.app.branch_manager.fork_branch(
                    source_id=parent_chat_id,
                    at_message_index=branch_idx,
                    name=name if name else None
                )
                fork_id = fork['id']
            except Exception as e:
                fork_id = str(uuid.uuid4())

            side_msgs = []
            parent_msg_content = msgs[branch_idx].get("content", "")
            side_msgs.append({
                "role": "system",
                "content": f"(Starting from selected response)\n\n{parent_msg_content}",
                "timestamp": datetime.now().strftime("%H:%M")
            })

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            side_fn = f"side_{fork_id}_{ts}.json"
            path = os.path.join(self.app.chat_manager.chats_directory, side_fn)

            data_obj = {
                "chat_id": fork_id,
                "chat_history": side_msgs,
                "conversation_summary": "",
                "token_count": sum(estimate_tokens(m.get("content","")) for m in side_msgs),
                "parent_chat_id": parent_chat_id,
                "root_chat_id": root_chat_id,
                "title": name if name else "",
                "branch_id": fork_id,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data_obj, f, indent=2, ensure_ascii=False)

            self.app.chat_manager.load_chat(side_fn)
            chat_display._refresh_chat()
            try:
                header = self.query_one("#header", HeaderBar)
                header.update_header()
            except Exception:
                pass
            try:
                sidebar = self.query_one("#left-sidebar", Sidebar)
                sidebar._build_chat_list()
            except Exception:
                pass
            chat_display.mount(Static(f"\n[dim]Branched to: {side_fn}[/dim]", markup=True))

        elif cmd == "branches":
            parent_id = self.app.chat_manager.current_chat.get("chat_id", self.app.chat_manager.current_chat_file)
            all_chats = self.app.chat_manager.get_chat_list()
            branches = [c for c in all_chats if c.get("parent_chat_id") == parent_id]
            if not branches:
                chat_display.mount(Static("\n[dim]No branches found[/dim]", markup=True))
            else:
                lines = [f"\n[bold]Branches:[/bold]"]
                for b in branches:
                    title = b.get("title", b["filename"].replace(".json", "").replace("_", " "))
                    lines.append(f"  \u25cb {title} ({b['filename']})")
                chat_display.mount(Static("\n".join(lines), markup=True))

        elif cmd == "temporary":
            self.app.temporary_mode = not self.app.temporary_mode
            mode = "ON" if self.app.temporary_mode else "OFF"
            chat_display.mount(Static(f"\n[dim]Temporary mode: {mode}[/dim]", markup=True))

        elif cmd == "incognito":
            self.app.incognito_mode = not self.app.incognito_mode
            mode = "ON" if self.app.incognito_mode else "OFF"
            chat_display.mount(Static(f"\n[dim]Incognito mode: {mode}[/dim]", markup=True))

        elif cmd == "cancel":
            if self.app._streaming:
                self.app._cancel_event.set()
                chat_display.mount(Static("\n[dim]Cancelling generation...[/dim]", markup=True))
            else:
                chat_display.mount(Static("\n[dim]No active generation to cancel[/dim]", markup=True))

        elif cmd == "context":
            provider = self.app.config.get("provider", "groq")
            model = self.app.config.get(f"{provider}_model", "")
            chat_tok = self.app.chat_manager.current_chat.get("token_count", 0)
            try:
                docs_counts = context_documents.get_context_token_count()
                docs_tok = int(docs_counts.get("total_tokens", 0))
            except Exception:
                docs_tok = 0
            total = chat_tok + docs_tok
            ctx_window = self.app.config.get_context_window(provider, model)
            chat_display.mount(Static(
                f"\n[bold]Context Usage:[/bold]\n"
                f"  Chat messages: {chat_tok} tokens\n"
                f"  Documents:     {docs_tok} tokens\n"
                f"  Total:         {total} / {ctx_window}\n"
                f"  Available:     {ctx_window - total} tokens",
                markup=True,
            ))

        elif cmd == "docs":
            subcmd = args[0].strip() if args else ""
            if subcmd == "list":
                docs = context_documents.list_documents()
                if not docs:
                    chat_display.mount(Static("\n[dim]No documents uploaded[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]Documents:[/bold]"]
                    for d in docs:
                        sel = "\u2713" if d.get("selected", False) else "\u25cb"
                        name = d.get("name", d.get("filename", "?"))
                        lines.append(f"  {sel} {name}")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            elif subcmd == "upload" and len(args) > 1:
                file_path = args[1].strip()
                if os.path.isfile(file_path):
                    with open(file_path, "rb") as f:
                        content = f.read()
                    fname = os.path.basename(file_path)
                    ok, meta = save_document(fname, content)
                    if ok:
                        chat_display.mount(Static(f"\n[dim]Document uploaded: {fname}[/dim]", markup=True))
                    else:
                        chat_display.mount(Static(f"\n[bold red]Failed to upload: {fname}[/bold red]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]File not found: {file_path}[/bold red]", markup=True))
            elif subcmd == "delete" and len(args) > 1:
                doc_name = args[1].strip()
                ok = delete_document(doc_name)
                if ok:
                    chat_display.mount(Static(f"\n[dim]Document deleted: {doc_name}[/dim]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]Failed to delete: {doc_name}[/bold red]", markup=True))
            elif subcmd == "select" and len(args) > 1:
                doc_name = args[1].strip()
                ok = context_documents.update_document_selection(doc_name, True)
                if ok:
                    chat_display.mount(Static(f"\n[dim]Document selected: {doc_name}[/dim]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]Failed to select: {doc_name}[/bold red]", markup=True))
            elif subcmd == "deselect" and len(args) > 1:
                doc_name = args[1].strip()
                ok = context_documents.update_document_selection(doc_name, False)
                if ok:
                    chat_display.mount(Static(f"\n[dim]Document deselected: {doc_name}[/dim]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]Failed to deselect: {doc_name}[/bold red]", markup=True))
            elif subcmd == "url" and len(args) > 1:
                url = args[1].strip()
                if not url.startswith(('http://', 'https://')):
                    chat_display.mount(Static("\n[bold red]URL must start with http:// or https://[/bold red]", markup=True))
                else:
                    try:
                        from readers.url_reader import UrlReader
                        text = UrlReader.extract_text(url)
                        if not text or not text.strip():
                            chat_display.mount(Static("\n[bold red]No text extracted from URL[/bold red]", markup=True))
                        else:
                            from urllib.parse import urlparse
                            parsed = urlparse(url)
                            name = f"{parsed.netloc}_{parsed.path.replace('/', '_')[:50]}.txt" or "web_page.txt"
                            ok, meta = save_document(name, text.encode('utf-8'))
                            if ok:
                                chat_display.mount(Static(f"\n[dim]URL ingested: {name}[/dim]", markup=True))
                            else:
                                chat_display.mount(Static(f"\n[bold red]Failed to save URL content[/bold red]", markup=True))
                    except ImportError:
                        chat_display.mount(Static("\n[bold red]URL ingestion requires: pip install requests beautifulsoup4[/bold red]", markup=True))
                    except Exception as e:
                        chat_display.mount(Static(f"\n[bold red]Error: {e}[/bold red]", markup=True))
            else:
                chat_display.mount(Static(
                    "\n[bold]Document Commands:[/bold]\n"
                    "  /docs list              - List documents\n"
                    "  /docs upload <path>     - Upload file\n"
                    "  /docs delete <name>     - Delete document\n"
                    "  /docs select <name>     - Include in context\n"
                    "  /docs deselect <name>   - Exclude from context\n"
                    "  /docs url <url>         - Ingest web page",
                    markup=True,
                ))

        elif cmd == "folders":
            subcmd = args[0].strip() if args else ""
            if subcmd == "list" or not subcmd:
                tree = self.app.folder_manager.get_folder_tree()
                if not tree:
                    chat_display.mount(Static("\n[dim]No folders[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]Folders:[/bold]"]
                    for f in tree:
                        lines.append(f"  \U0001f4c1 {f['name']} (id: {f['id']})")
                        for c in f.get("children", []):
                            lines.append(f"    \U0001f4c2 {c['name']} (id: {c['id']})")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            elif subcmd == "create":
                name = args[1].strip() if len(args) > 1 else "New Folder"
                parent_id = args[2].strip() if len(args) > 2 else None
                folder = self.app.folder_manager.create_folder(name, parent_id)
                chat_display.mount(Static(f"\n[dim]Created folder: {folder['name']} (id: {folder['id']})[/dim]", markup=True))
                try:
                    sidebar = self.query_one("#left-sidebar", Sidebar)
                    sidebar._build_folder_tree()
                except Exception:
                    pass
            elif subcmd == "contents" and len(args) > 1:
                folder_id = args[1].strip()
                contents = self.app.folder_manager.get_folder_contents(folder_id)
                chats = contents.get("chats", [])
                if not chats:
                    chat_display.mount(Static("\n[dim]Folder is empty[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]Folder contents:[/bold]"]
                    for fn in chats:
                        path = os.path.join(self.app.chat_manager.chats_directory, fn)
                        if os.path.exists(path):
                            try:
                                with open(path, "r", encoding="utf-8") as f:
                                    data = json.load(f)
                                title = data.get("title", fn.replace(".json", "").replace("_", " "))
                                lines.append(f"  \u25cb {title}")
                            except Exception:
                                lines.append(f"  \u25cb {fn}")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            elif subcmd == "assign" and len(args) > 2:
                folder_id = args[1].strip()
                chat_file = args[2].strip()
                if not chat_file.endswith('.json'):
                    chat_file += '.json'
                ok = self.app.folder_manager.assign_chat_to_folder(chat_file, folder_id)
                if ok:
                    chat_display.mount(Static(f"\n[dim]Assigned {chat_file} to folder[/dim]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]Failed to assign chat[/bold red]", markup=True))
            else:
                chat_display.mount(Static(
                    "\n[bold]Folder Commands:[/bold]\n"
                    "  /folders list                  - List folders\n"
                    "  /folders create <name> [parent] - Create folder\n"
                    "  /folders contents <id>         - Show folder contents\n"
                    "  /folders assign <id> <chat>    - Assign chat to folder",
                    markup=True,
                ))

        elif cmd == "tools":
            subcmd = args[0].strip() if args else ""
            if subcmd == "enable" and len(args) > 1:
                provider = args[1].strip()
                self.app.config.set(f'{provider}_tools_enabled', True)
                self.app.config.save_config()
                chat_display.mount(Static(f"\n[dim]Tools enabled for {provider}[/dim]", markup=True))
            elif subcmd == "disable" and len(args) > 1:
                provider = args[1].strip()
                self.app.config.set(f'{provider}_tools_enabled', False)
                self.app.config.save_config()
                chat_display.mount(Static(f"\n[dim]Tools disabled for {provider}[/dim]", markup=True))
            elif subcmd == "os" and len(args) > 1:
                tool_os = args[1].strip().lower()
                if tool_os in ('windows', 'linux', 'macos'):
                    self.app.config.set('tool_os', tool_os)
                    self.app.config.save_config()
                    chat_display.mount(Static(f"\n[dim]Tool OS set to {tool_os}[/dim]", markup=True))
                else:
                    chat_display.mount(Static("\n[bold red]Invalid OS. Use: windows, linux, macos[/bold red]", markup=True))
            elif subcmd == "status":
                provider = self.app.config.get("provider", "unknown")
                enabled = self.app.config.get(f'{provider}_tools_enabled', False)
                tool_os = self.app.config.get('tool_os', 'windows')
                chat_display.mount(Static(
                    f"\n[bold]Tools:[/bold]\n"
                    f"  Provider: {provider}\n"
                    f"  Enabled:  {'Yes' if enabled else 'No'}\n"
                    f"  OS:       {tool_os}",
                    markup=True,
                ))
            else:
                chat_display.mount(Static(
                    "\n[bold]Tool Commands:[/bold]\n"
                    "  /tools enable <provider>   - Enable tools\n"
                    "  /tools disable <provider>  - Disable tools\n"
                    "  /tools os <os>             - Set OS (windows/linux/macos)\n"
                    "  /tools status              - Show tool config",
                    markup=True,
                ))

        elif cmd == "toolbox":
            subcmd = args[0].strip() if args else ""
            toolbox_dir = os.path.join(self.app.project_root, 'toolbox')
            default_toolbox = os.path.join(self.app.project_root, 'default_toolbox')

            if subcmd == "list":
                os.makedirs(toolbox_dir, exist_ok=True)
                files = []
                if os.path.isdir(default_toolbox):
                    for n in sorted(os.listdir(default_toolbox)):
                        if not n.startswith('.') and os.path.isfile(os.path.join(default_toolbox, n)):
                            files.append((n, "default"))
                for n in sorted(os.listdir(toolbox_dir)):
                    if not n.startswith('.') and os.path.isfile(os.path.join(toolbox_dir, n)):
                        files.append((n, "custom"))
                if not files:
                    chat_display.mount(Static("\n[dim]No toolbox scripts[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]Toolbox:[/bold]"]
                    for name, source in files:
                        tag = "[custom]" if source == "custom" else "[default]"
                        lines.append(f"  {tag} {name}")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            elif subcmd == "view" and len(args) > 1:
                fname = args[1].strip()
                custom = os.path.join(toolbox_dir, fname)
                default_p = os.path.join(default_toolbox, fname)
                fpath = custom if os.path.isfile(custom) else (default_p if os.path.isfile(default_p) else None)
                if fpath:
                    with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    chat_display.mount(Static(f"\n[bold]{fname}:[/bold]\n```\n{content[:2000]}\n```", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]File not found: {fname}[/bold red]", markup=True))
            else:
                chat_display.mount(Static(
                    "\n[bold]Toolbox Commands:[/bold]\n"
                    "  /toolbox list       - List scripts\n"
                    "  /toolbox view <file> - View script content",
                    markup=True,
                ))

        elif cmd == "toolbelt":
            subcmd = args[0].strip() if args else ""
            chat_file = self.app.chat_manager.current_chat_file
            if not chat_file:
                chat_display.mount(Static("\n[bold red]No chat loaded[/bold red]", markup=True))
                return

            if subcmd == "list":
                tb = self.app.chat_manager.current_chat.get("toolbelt", {})
                if isinstance(tb, list):
                    tb = {s: {} for s in tb}
                if not tb:
                    chat_display.mount(Static("\n[dim]No scripts in toolbelt[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]Toolbelt:[/bold]"]
                    for script in tb:
                        lines.append(f"  \u25cb {script}")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            elif subcmd == "add" and len(args) > 1:
                script = args[1].strip()
                tb = self.app.chat_manager.current_chat.setdefault("toolbelt", {})
                if isinstance(tb, list):
                    tb = {s: {} for s in tb}
                    self.app.chat_manager.current_chat["toolbelt"] = tb
                tb[script] = {}
                self.app.chat_manager.save_current_chat(force_save=True)
                chat_display.mount(Static(f"\n[dim]Added {script} to toolbelt[/dim]", markup=True))
            elif subcmd == "remove" and len(args) > 1:
                script = args[1].strip()
                tb = self.app.chat_manager.current_chat.get("toolbelt", {})
                if isinstance(tb, list):
                    tb = {s: {} for s in tb}
                if script in tb:
                    del tb[script]
                    self.app.chat_manager.current_chat["toolbelt"] = tb
                    self.app.chat_manager.save_current_chat(force_save=True)
                    chat_display.mount(Static(f"\n[dim]Removed {script} from toolbelt[/dim]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]{script} not in toolbelt[/bold red]", markup=True))
            else:
                chat_display.mount(Static(
                    "\n[bold]Toolbelt Commands:[/bold]\n"
                    "  /toolbelt list            - Show assigned scripts\n"
                    "  /toolbelt add <script>    - Add script\n"
                    "  /toolbelt remove <script> - Remove script",
                    markup=True,
                ))

        elif cmd == "endpoints":
            subcmd = args[0].strip() if args else ""
            if subcmd == "list":
                endpoints = self.app.config.get("custom_endpoints", {})
                if not endpoints:
                    chat_display.mount(Static("\n[dim]No custom endpoints[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]Custom Endpoints:[/bold]"]
                    for eid, ecfg in endpoints.items():
                        lines.append(f"  {eid}: {ecfg.get('base_url', '?')} ({ecfg.get('name', eid)})")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            elif subcmd == "add" and len(args) > 2:
                endpoint_name = args[1]
                base_url = args[2]
                if endpoint_name and base_url:
                    eid = re.sub(r'[^a-z0-9]+', '_', endpoint_name.lower()).strip('_')
                    self.app.config.save_endpoint(eid, {
                        "name": endpoint_name,
                        "base_url": base_url.rstrip("/"),
                        "api_key_env": re.sub(r'[^A-Z0-9]+', '_', endpoint_name.upper()).strip('_') + "_API_KEY",
                        "api_key": "",
                        "default_model": "",
                        "context_window": 32768,
                    })
                    chat_display.mount(Static(f"\n[dim]Added endpoint: {eid}[/dim]", markup=True))
                else:
                    chat_display.mount(Static("\n[bold red]Usage: /endpoints add <name> <base_url>[/bold red]", markup=True))
            elif subcmd == "delete" and len(args) > 1:
                eid = args[1].strip()
                if self.app.config.delete_endpoint(eid):
                    chat_display.mount(Static(f"\n[dim]Deleted endpoint: {eid}[/dim]", markup=True))
                else:
                    chat_display.mount(Static(f"\n[bold red]Endpoint not found: {eid}[/bold red]", markup=True))
            else:
                chat_display.mount(Static(
                    "\n[bold]Endpoint Commands:[/bold]\n"
                    "  /endpoints list              - List endpoints\n"
                    "  /endpoints add <name> <url>  - Add endpoint\n"
                    "  /endpoints delete <id>       - Delete endpoint",
                    markup=True,
                ))

        elif cmd == "prompts":
            subcmd = args[0].strip() if args else ""
            prompts_dir = self.app.project_root / "prompts"

            if subcmd == "list" or not subcmd:
                prompts = []
                for fname in ["default_prompts.jsonl", "custom_prompts.jsonl"]:
                    fpath = prompts_dir / fname
                    if fpath.exists():
                        with open(fpath, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    try:
                                        entry = json.loads(line)
                                        prompts.append(entry)
                                    except json.JSONDecodeError:
                                        pass
                if not prompts:
                    chat_display.mount(Static("\n[dim]No prompts[/dim]", markup=True))
                else:
                    lines = [f"\n[bold]System Prompts:[/bold]"]
                    for p in prompts:
                        pid = p.get("id", "?")
                        title = p.get("title", p.get("name", "?"))
                        lines.append(f"  {pid}: {title}")
                    chat_display.mount(Static("\n".join(lines), markup=True))
            else:
                chat_display.mount(Static(
                    "\n[bold]Prompt Commands:[/bold]\n"
                    "  /prompts list   - List all prompts",
                    markup=True,
                ))

    def _send_message(self, text: str):
        chat_display = self.query_one("#chat-display", ChatDisplay)
        chat_display.append_message("user", text)
        chat_display.show_thinking()
        self.app.run_chat_turn(text)
