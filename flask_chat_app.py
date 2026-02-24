"""
Flask-based AI Chat Application (stable routes, binary-safe uploads, llama.cpp load/unload)
"""
from __future__ import annotations
import os
import json
import subprocess
import threading
import time
import re
import shlex
import uuid
from urllib.parse import urlparse
from datetime import datetime
from typing import Dict, List
import requests
from api_clients import estimate_tokens, get_llamacpp_context_size
from context_documents import context_documents  

from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from flask_cors import CORS
import webbrowser
from werkzeug.serving import make_server

# Optional webview
try:
    import webview
    WEBVIEW_AVAILABLE = True
    print("pywebview found - will use standalone window")
except Exception as e:
    WEBVIEW_AVAILABLE = False
    print(f"pywebview unavailable: {e}")

# App modules
from config_manager import ConfigManager
from chat_manager import ChatManager
from api_clients import (
    call_groq_stream, call_google_stream,
    call_mistral_stream, call_openrouter_stream, call_llamacpp_stream,
)
from context_documents import (
    list_documents, save_document, delete_document, get_document,
)


class FlaskChatApp:
    def __init__(self):
        # figure out where the repo root is (same folder that has templates/, static/, prompts/)
        repo_root = os.path.abspath(os.path.dirname(__file__))

        # tell Flask where to look for templates and static files
        self.app = Flask(
            __name__,
            template_folder=os.path.join(repo_root, "templates"),
            static_folder=os.path.join(repo_root, "static"),
        )
        self.app.secret_key = os.urandom(24)
        CORS(self.app)

        self.config = ConfigManager()
        # No longer auto-saves API keys to config.json - they're read from environment variables
        self.chat_manager = ChatManager()

        self.cancel_generation = False
        self.temporary_mode = False
        self.incognito_mode = False
        self.available_providers = ["groq", "google", "mistral", "openrouter", "llamacpp"]
        self.last_renamed_chat = None

        self.pending_messages: Dict[int, Dict[str, str]] = {}
        self._single_model_mode = False  # True when running a dedicated single-model server
        self._single_model_loading = False  # True while the background thread is starting the server

        self.setup_routes()
        self.server = None

    # ---------------- Routes ----------------
    def setup_routes(self):
        app = self.app

        @app.route('/')
        def index():
            ver = datetime.now().strftime("%Y%m%d%H%M%S")  # unique cache-buster each restart
            return render_template('chat.html', version=ver)  # keep your existing HTML/theme

        @app.route('/static/<path:filename>')
        def static_files(filename):
            return send_from_directory(self.app.static_folder, filename)


        @app.route('/prompts/<path:filename>')
        def prompts_files(filename):
            return send_from_directory('prompts', filename)

        @app.route('/api/config')
        def get_config():
            current_provider = self.config.get("provider", "groq")
            models = self.get_provider_models(current_provider)

            current_model = self.config.get(f"{current_provider}_model", "")
            if not current_model or current_model not in models:
                # Fallback to first available model
                current_model = models[0] if models else ""
                if current_model:
                    self.config.set(f"{current_provider}_model", current_model)
                    self.config.save_config()

            return jsonify({
                "providers": self.available_providers,
                "current_provider": current_provider,
                "models": models,
                "current_model": current_model,
                "temperature": self.config.get(f"{current_provider}_temperature", 0.7),
                "max_tokens": self.config.get(f"{current_provider}_max_tokens", 4096),  # changed
                "system_prompt": self.config.get(f"{current_provider}_system_prompt", ""),
                "temporary_mode": self.temporary_mode,
                "incognito_mode": self.incognito_mode,
            })

        @app.route('/api/models/<provider>')
        def get_models(provider: str):
            models = self.get_provider_models(provider)
            current_model = self.config.get(f"{provider}_model", (models[0] if models else ""))
            return jsonify({
                "models": models,
                "current_model": current_model,
                "temperature": self.config.get(f"{provider}_temperature", 0.7),
                "max_tokens": self.config.get(f"{provider}_max_tokens", 4096),  # changed
                "system_prompt": self.config.get(f"{provider}_system_prompt", ""),
            })

        # --- Model list: save custom list for provider ---
        @app.route('/api/models/<provider>/save', methods=['POST'])
        def save_models_for_provider(provider: str):
            data = request.get_json() or {}
            # Accept either "models" (array) or "models_text" (newline string)
            models = data.get('models')
            if not isinstance(models, list):
                text = (data.get('models_text') or '').strip()
                models = [line.strip() for line in text.splitlines() if line.strip()]

            # De-duplicate while preserving order
            seen = set()
            cleaned = []
            for m in models:
                if isinstance(m, str):
                    m = m.strip()
                    if m and m not in seen:
                        seen.add(m)
                        cleaned.append(m)

            # Save as the explicit custom list so it wins precedence
            self.config.set(f"custom_{provider}_models", cleaned)

            # If current model is no longer present, pick the first one (or empty)
            current = self.config.get(f"{provider}_model", "")
            if current not in cleaned:
                new_current = (cleaned[0] if cleaned else "")
                self.config.set(f"{provider}_model", new_current)
            else:
                new_current = current

            self.config.save_config()

            # Return same shape as GET for convenience
            return jsonify({
                "success": True,
                "models": cleaned,
                "current_model": new_current,
                "temperature": self.config.get(f"{provider}_temperature", 0.7),
                "max_tokens": self.config.get_model_max_tokens(provider, new_current),
                "system_prompt": self.config.get(f"{provider}_system_prompt", ""),
            })

        # --- Model list: reset provider back to defaults (remove custom/stored) ---
        @app.route('/api/models/<provider>/reset', methods=['POST'])
        def reset_models_for_provider(provider: str):
            # Remove any custom and stored lists so we fall back to defaults/recent
            # (ConfigManager precedence: custom > stored > recent > defaults)
            try:
                # Access the raw dict to delete keys safely
                cfg = self.config.config
                cfg.pop(f"custom_{provider}_models", None)
                cfg.pop(f"stored_{provider}_models", None)
                self.config.save_config()
            except Exception:
                pass

            # Recompute the list by asking our helper (will now use defaults or recent)
            models = self.get_provider_models(provider)  # same helper used by GET
            new_current = self.config.get(f"{provider}_model", "")
            if new_current not in models:
                new_current = (models[0] if models else "")
                self.config.set(f"{provider}_model", new_current)
                self.config.save_config()

            return jsonify({
                "success": True,
                "models": models,
                "current_model": new_current,
                "temperature": self.config.get(f"{provider}_temperature", 0.7),
                "max_tokens": self.config.get(f"{provider}_max_tokens", 4096),
                "system_prompt": self.config.get(f"{provider}_system_prompt", ""),
            })

        # --- Per-model CRUD routes ---

        @app.route('/api/models/<provider>/settings', methods=['GET'])
        def get_provider_model_settings(provider):
            settings = self.config.get_all_model_settings_for_provider(provider)
            return jsonify({"success": True, "settings": settings})

        @app.route('/api/models/<provider>/settings/<path:model>', methods=['GET'])
        def get_single_model_settings(provider, model):
            settings = self.config.get_model_settings(provider, model)
            return jsonify({"success": True, "settings": settings})

        @app.route('/api/models/<provider>/settings/<path:model>', methods=['POST'])
        def set_single_model_settings(provider, model):
            data = request.get_json() or {}
            try:
                out = {}
                if "max_tokens" in data:
                    mt = int(data["max_tokens"])
                    if mt <= 0: return jsonify({"success": False, "error": "max_tokens must be > 0"}), 400
                    out["max_tokens"] = mt
                if "temperature" in data:
                    out["temperature"] = float(data["temperature"])
                if "top_p" in data:
                    out["top_p"] = float(data["top_p"])
                if "top_k" in data:
                    out["top_k"] = int(data["top_k"])
                if "system_prompt" in data:
                    out["system_prompt"] = str(data["system_prompt"])
                if "context_window" in data:
                    cw = int(data["context_window"])
                    if cw > 0:
                        out["context_window"] = cw
                if "n_gpu_layers" in data:
                    ngl = int(data["n_gpu_layers"])
                    if ngl >= -1:  # -1 = all layers (same as 99)
                        out["n_gpu_layers"] = ngl
                if "vram_required_gb" in data:
                    vram = float(data["vram_required_gb"])
                    if vram >= 0:
                        out["vram_required_gb"] = vram
                if not out:
                    return jsonify({"success": False, "error": "no valid fields"}), 400
                self.config.set_model_settings(provider, model, out)
                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 400

        @app.route('/api/models/<provider>/add', methods=['POST'])
        def add_custom_model(provider):
            data = request.get_json() or {}
            model = (data.get("model") or "").strip()
            if not model:
                return jsonify({"success": False, "error": "missing model"}), 400
            key = f"custom_{provider}_models"
            lst = list(self.config.get(key, []))
            if model not in lst:
                lst.append(model)
                self.config.set(key, lst)
                self.config.save_config()
            return jsonify({"success": True, "models": lst})

        @app.route('/api/models/<provider>/delete/<path:model>', methods=['DELETE'])
        def delete_custom_model(provider, model):
            """
            Delete a model from ANY model list (custom, stored, or both).
            Also deletes any saved settings for this model.
            """
            try:
                removed_from = []
                
                # Try removing from custom models list
                custom_key = f"custom_{provider}_models"
                custom_list = list(self.config.get(custom_key, []))
                if model in custom_list:
                    custom_list.remove(model)
                    self.config.set(custom_key, custom_list)
                    removed_from.append("custom")
                
                # Try removing from stored models list  
                stored_key = f"stored_{provider}_models"
                stored_list = list(self.config.get(stored_key, []))
                if model in stored_list:
                    stored_list.remove(model)
                    self.config.set(stored_key, stored_list)
                    removed_from.append("stored")
                
                # If not found in either list, still allow deletion (might be a default)
                # We'll just mark it as deleted and remove settings
                if not removed_from:
                    removed_from.append("default")
                
                # Delete the model's settings
                settings_key = f"{provider}_model_settings"
                all_settings = self.config.get(settings_key, {})
                if model in all_settings:
                    del all_settings[model]
                    self.config.set(settings_key, all_settings)
                
                # If this was the currently selected model, switch to first available
                current = self.config.get(f"{provider}_model", "")
                if current == model:
                    # Get remaining models from any source
                    remaining = self.get_provider_models(provider)
                    new_current = remaining[0] if remaining else ""
                    self.config.set(f"{provider}_model", new_current)
                
                # Save all changes
                self.config.save_config()
                
                return jsonify({
                    "success": True, 
                    "removed_from": removed_from,
                    "message": f"Model removed from {', '.join(removed_from)} list(s)"
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/browse/<provider>/toggle', methods=['POST'])
        def toggle_browse_model(provider):
            """Add/remove model selection and keep provider dropdown synced to browse checkboxes."""
            BROWSEABLE = ("openrouter", "groq", "google", "mistral", "llamacpp")
            if provider not in BROWSEABLE:
                return jsonify({"success": False, "error": f"Provider '{provider}' not browseable"}), 400

            data = request.get_json() or {}
            model = (data.get("model") or "").strip()
            checked = bool(data.get("checked", True))
            if not model:
                return jsonify({"success": False, "error": "missing model"}), 400

            stored_key = f"stored_{provider}_models"
            custom_key = f"custom_{provider}_models"

            # Start from current selected set, preferring stored -> custom -> current provider list.
            lst = list(self.config.get(stored_key, []) or [])
            if not lst:
                lst = list(self.config.get(custom_key, []) or [])
            if not lst:
                lst = list(self.get_provider_models(provider))

            if checked:
                if model not in lst:
                    lst.append(model)
            else:
                lst = [m for m in lst if m != model]

            # Keep both stored and custom lists aligned so /api/models reflects browse checks immediately.
            self.config.set(stored_key, lst)
            self.config.set(custom_key, list(lst))

            # If active model was unchecked, select the first remaining model (or blank).
            current = self.config.get(f"{provider}_model", "")
            if current not in lst:
                self.config.set(f"{provider}_model", lst[0] if lst else "")

            self.config.save_config()
            return jsonify({"success": True, "models": lst})

        @app.route('/api/browse/<provider>/catalog')
        def get_browse_catalog(provider):
            BROWSEABLE = ("openrouter", "groq", "google", "mistral", "llamacpp")
            if provider not in BROWSEABLE:
                return jsonify({"success": False, "error": f"Provider '{provider}' not browseable"}), 400
            return jsonify(self.config.get(f"{provider}_catalog", []))

        @app.route('/api/browse/<provider>/refresh', methods=['POST'])
        def refresh_browse_catalog(provider):
            BROWSEABLE = ("openrouter", "groq", "google", "mistral", "llamacpp")
            if provider not in BROWSEABLE:
                return jsonify({"success": False, "error": f"Provider '{provider}' not browseable"}), 400
            if provider == "llamacpp":
                # Scan models directory via SSH
                scanned = _ssh_scan_models_dir(self)
                if scanned:
                    self.config.set("llamacpp_catalog", scanned)
                    self.config.save_config()
                    return jsonify({"success": True, "count": len(scanned)})
                return jsonify({"success": False, "error": "Failed to scan models directory via SSH"}), 502
            if provider == "openrouter":
                from api_clients import fetch_openrouter_catalog
                catalog = fetch_openrouter_catalog()
            else:
                # Groq, Google, Mistral all need an API key
                env_map = {"groq": "GROQ_API_KEY", "google": "GOOGLE_API_KEY", "mistral": "MISTRAL_API_KEY"}
                env_var = env_map.get(provider, "")
                api_key = os.getenv(env_var, "") or self.config.get(f"{provider}_api_key", "")
                if not api_key or api_key.startswith("your_"):
                    return jsonify({"success": False, "error": f"{provider} API key not configured"}), 400
                if provider == "groq":
                    from api_clients import fetch_groq_catalog
                    catalog = fetch_groq_catalog(api_key)
                elif provider == "google":
                    from api_clients import fetch_google_catalog
                    catalog = fetch_google_catalog(api_key)
                elif provider == "mistral":
                    from api_clients import fetch_mistral_catalog
                    catalog = fetch_mistral_catalog(api_key)
                else:
                    catalog = []
            if not catalog:
                return jsonify({"success": False, "error": f"Failed to fetch catalog from {provider}"}), 502
            self.config.set(f"{provider}_catalog", catalog)
            self.config.save_config()
            return jsonify({"success": True, "count": len(catalog)})

        @app.route('/api/chat/history')
        def get_chat_history():
            chats = self.chat_manager.get_chat_list()
            return jsonify({"chats": chats, "current_chat": self.chat_manager.current_chat_file})

        @app.route('/api/chat/messages')
        def get_chat_messages():
            return jsonify({"messages": self.chat_manager.get_messages()})

        @app.route('/api/chat/load/<path:filename>')
        def load_chat(filename):
            if not filename.endswith('.json'):
                filename += '.json'
            ok = self.chat_manager.load_chat(filename)
            if not ok:
                return jsonify({"success": False, "error": f"Failed to load chat: {filename}"}), 400
            title = filename[:-5]
            if self.chat_manager.current_chat:
                title = self.chat_manager.current_chat.get('title', title)
            return jsonify({"success": True, "messages": self.chat_manager.get_messages(), "title": title})

        @app.route('/api/chat/new', methods=['POST'])
        def new_chat():
            try:
                if self.temporary_mode:
                    self.chat_manager.clear_current_chat(auto_save=False)

                filename = self.chat_manager.create_new_chat()

                # Verify the file actually exists on disk before declaring success
                chat_path = os.path.join(self.chat_manager.chats_directory, filename)
                if not os.path.exists(chat_path):
                    # Surface a clear backend error — otherwise the UI believes it's saved
                    return jsonify({
                        "success": False,
                        "error": f"Failed to save new chat file: {filename}"
                    }), 500

                return jsonify({"success": True, "filename": filename})
            except Exception as e:
                # Defensive catch to avoid silent successes
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/chat/delete/<filename>', methods=['DELETE'])
        def delete_chat(filename):
            return jsonify({"success": self.chat_manager.delete_chat(filename)})

        @app.route('/api/config/update', methods=['POST'])
        def update_config():
            data = request.get_json() or {}
            provider = data.get('provider', self.config.get("provider"))

            if 'provider' in data:
                self.config.set("provider", provider)
            if 'model' in data:
                self.config.set(f"{provider}_model", data['model'])
                self.config.add_recent_model(provider, data['model'])
            if 'temperature' in data:
                self.config.set(f"{provider}_temperature", float(data['temperature']))
            if 'system_prompt' in data:
                self.config.set(f"{provider}_system_prompt", data['system_prompt'])
            if 'temporary_mode' in data:
                self.temporary_mode = bool(data['temporary_mode'])
            if 'incognito_mode' in data:
                self.incognito_mode = bool(data['incognito_mode'])

            # --- NEW FEATURE: Save custom model list ---
            if 'custom_models' in data:
                cm = data['custom_models'] or {}
                p = cm.get('provider', provider)
                models = [m.strip() for m in (cm.get('models') or []) if m and m.strip()]
                self.config.set(f"custom_{p}_models", models)
                # Ensure valid current model
                current = self.config.get(f"{p}_model")
                if models and current not in models:
                    self.config.set(f"{p}_model", models[0])

            # --- NEW FEATURE: Reset custom model list ---
            if 'reset_custom_models' in data:
                rc = data['reset_custom_models']
                p = rc.get('provider', provider) if isinstance(rc, dict) else provider
                self.config.config.pop(f"custom_{p}_models", None)

            self.config.save_config()
            return jsonify({"success": True})

        @app.route('/api/chat/send', methods=['POST'])
        def send_message():
            data = request.get_json() or {}
            message = (data.get('message') or '').strip()
            if not message:
                return jsonify({"success": False, "error": "Empty message"}), 400

            provider = self.config.get("provider")
            model = self.config.get(f"{provider}_model")

            # pick effective settings: request -> per-model -> provider defaults
            model_settings = self.config.get_model_settings(provider, model)
            temperature = float(
                data.get('temperature',
                    model_settings.get('temperature',
                        self.config.get(f"{provider}_temperature", 0.7)))
            )
            max_tokens = int(
                data.get('max_tokens',
                    model_settings.get('max_tokens',
                        self.config.get(f"{provider}_max_tokens", 4096)))
            )

            # global prompt "" (None) means: use model-specific if present
            req_system_prompt = data.get('system_prompt')
            if req_system_prompt is None:
                # older UIs may omit the field; fall back to provider default
                system_prompt = self.config.get(f"{provider}_system_prompt", "")
            elif req_system_prompt == "":
                system_prompt = model_settings.get('system_prompt', "")
            else:
                system_prompt = str(req_system_prompt)

            # persist snapshot used for this message id
            mid = int(time.time() * 1000)
            self.pending_messages[mid] = {
                "provider": provider,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
            }
            
            # Add optional parameters if they exist
            if "top_p" in model_settings:
                self.pending_messages[mid]["top_p"] = model_settings["top_p"]
            if "top_k" in model_settings:
                self.pending_messages[mid]["top_k"] = model_settings["top_k"]

            # save current as well
            self.config.set("provider", provider)
            self.config.set(f"{provider}_model", model)
            self.config.set(f"{provider}_temperature", temperature)
            # Removed: max_tokens auto-save to prevent config pollution
            # self.config.set(f"{provider}_max_tokens", max_tokens)
            self.config.set(f"{provider}_system_prompt", system_prompt)
            self.config.save_config()

            # add user message unless incognito
            if not self.incognito_mode:
                # Track the current chat file before adding the message
                old_filename = self.chat_manager.current_chat_file
                self.chat_manager.add_message("user", message)
                # Check if the chat was auto-renamed (first message)
                new_filename = self.chat_manager.current_chat_file
                if old_filename != new_filename:
                    # Chat was auto-renamed, store the new filename to return it
                    self.last_renamed_chat = new_filename
                else:
                    self.last_renamed_chat = None
            else:
                self.last_renamed_chat = None

            # selected context indices from UI
            self.has_selected_context = ('selected_context' in data)
            self.selected_context = data.get('selected_context') if self.has_selected_context else None
            self.selected_summaries = data.get('selected_summaries', [])

            print("SEND PAYLOAD:", {"provider": provider, "model": model, "mid": mid})
            response_data = {"success": True, "message_id": mid, "current_chat_file": self.chat_manager.current_chat_file}
            # Include the renamed chat file if applicable
            if hasattr(self, 'last_renamed_chat') and self.last_renamed_chat:
                response_data["filename"] = self.last_renamed_chat
                # Reset the flag
                self.last_renamed_chat = None
            return jsonify(response_data)

        @app.route('/api/chat/context-check', methods=['POST'])
        def context_check():
            """Estimate input tokens and check against context window before sending."""
            data = request.get_json() or {}
            message = (data.get('message') or '').strip()
            selected_context = data.get('selected_context')
            selected_summaries = data.get('selected_summaries', [])
            context_bar_open = data.get('context_bar_open', False)

            provider = self.config.get("provider")
            model = self.config.get(f"{provider}_model")
            model_settings = self.config.get_model_settings(provider, model)

            # Build api_messages the same way stream_response does
            if self.incognito_mode:
                api_messages = [{"role": "user", "content": message}]
            elif context_bar_open and selected_context is not None:
                if len(selected_context) > 0 or len(selected_summaries) > 0:
                    api_messages = self.chat_manager.get_selected_context(
                        selected_context, selected_summaries
                    )
                else:
                    api_messages = [{"role": "user", "content": message}]
            else:
                api_messages = self.chat_manager.get_conversation_context()

            # Add the new message (if not already in context from get_conversation_context)
            new_msg_tokens = estimate_tokens(message)

            # System prompt
            system_prompt = model_settings.get('system_prompt',
                self.config.get(f"{provider}_system_prompt", ""))
            system_prompt_tokens = estimate_tokens(system_prompt) if system_prompt else 0

            # Conversation tokens
            conversation_tokens = sum(estimate_tokens(m.get("content", "")) for m in api_messages)

            # Document tokens
            docs = context_documents.build_context_injections()
            doc_tokens = sum(estimate_tokens(m.get("content", "")) for m in docs)

            total_tokens = system_prompt_tokens + conversation_tokens + doc_tokens + new_msg_tokens

            # Context window
            context_window = self.config.get_context_window(provider, model)
            # For llamacpp, try auto-detecting if no override configured
            if provider == "llamacpp":
                ms = self.config.get_model_settings(provider, model)
                if "context_window" not in ms or not ms["context_window"]:
                    auto = get_llamacpp_context_size(self.config.config)
                    if auto > 0:
                        context_window = auto

            max_output = int(model_settings.get('max_tokens',
                self.config.get(f"{provider}_max_tokens", 4096)))
            # Clamp max_output so it never consumes the entire context window
            if max_output >= context_window:
                max_output = max(1, context_window // 2)
            available_input = context_window - max_output
            overflow = total_tokens > available_input
            overflow_amount = max(0, total_tokens - available_input)

            return jsonify({
                "total_tokens": total_tokens,
                "context_window": context_window,
                "max_output_tokens": max_output,
                "available_input": available_input,
                "overflow": overflow,
                "overflow_amount": overflow_amount,
                "breakdown": {
                    "system_prompt": system_prompt_tokens,
                    "conversation": conversation_tokens,
                    "documents": doc_tokens,
                    "new_message": new_msg_tokens,
                }
            })

        @app.route('/api/chat/stream/<int:message_id>')
        def stream_response(message_id: int):
            def generate():
                try:
                    # Build API messages
                    if self.incognito_mode:
                        msgs = self.chat_manager.get_messages()
                        if msgs and msgs[-1]["role"] == "user":
                            api_messages = [{"role": "user", "content": msgs[-1]["content"]}]
                        else:
                            yield f"data: {json.dumps({'type':'error','content':'No message found'})}\n\n"
                            return
                    else:
                        if getattr(self, 'has_selected_context', False):
                            # Client explicitly provided selection
                            selected_msgs = self.selected_context or []
                            selected_sums = getattr(self, 'selected_summaries', [])

                            if len(selected_msgs) > 0 or len(selected_sums) > 0:
                                api_messages = self.chat_manager.get_selected_context(
                                    selected_msgs,
                                    selected_sums
                                )
                            else:
                                # Explicit NONE => only the latest user prompt (no history)
                                msgs = self.chat_manager.get_messages()
                                # Use the newest user message; if not found, send empty content error
                                last_user = next((m for m in reversed(msgs) if m.get("role") == "user"), None)
                                if not last_user:
                                    yield f"data: {json.dumps({'type':'error','content':'No user message found'})}\n\n"
                                    return
                                api_messages = [{"role": "user", "content": last_user.get("content", "")}]
                        else:
                            # No explicit selection provided: use normal conversation context
                            api_messages = self.chat_manager.get_conversation_context()

                        # ALWAYS include documents, regardless of selection mode
                        docs = context_documents.build_context_injections()
                        api_messages.extend(docs)

                        # (optional) clear selection flags after use so they don't leak into future sends
                        self.selected_context = None
                        self.has_selected_context = False

                    snap = self.pending_messages.pop(message_id, None)
                    if snap is None:
                        provider = self.config.get("provider")
                        model = self.config.get(f"{provider}_model")
                        ms = self.config.get_model_settings(provider, model)
                        temperature = self.config.get(f"{provider}_temperature", 0.7)
                        temperature = ms.get("temperature", temperature)
                        max_tokens = self.config.get(f"{provider}_max_tokens", 4096)
                        max_tokens = ms.get("max_tokens", max_tokens)
                        system_prompt = self.config.get(f"{provider}_system_prompt", "")
                        # if the last request set system_prompt=="" (global 'None'), prefer model-specific
                        if not system_prompt and ms.get("system_prompt"):
                            system_prompt = ms["system_prompt"]
                    else:
                        provider = snap['provider']
                        model = snap['model']
                        temperature = snap['temperature']
                        max_tokens = snap['max_tokens']
                        system_prompt = snap['system_prompt']

                    stream_func = {
                        "groq": call_groq_stream,
                        "google": call_google_stream,
                        "mistral": call_mistral_stream,
                        "openrouter": call_openrouter_stream,
                        "llamacpp": call_llamacpp_stream,
                    }.get(provider)
                    if not stream_func:
                        yield f"data: {json.dumps({'type':'error','content':f'Unknown provider: {provider}'})}\n\n"
                        return

                    # Send model info first
                    yield f"data: {json.dumps({'type':'model','content':model})}\n\n"
                    self.cancel_generation = False

                    # Build merged config to pass to API clients
                    merged_cfg = dict(self.config.config)
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
                    # Optional per-model sampling params
                    ms = self.config.get_model_settings(provider, model)
                    if "top_p" in ms: merged_cfg["top_p"] = ms["top_p"]
                    if "top_k" in ms: merged_cfg["top_k"] = ms["top_k"]

                    full = ""
                    for chunk in stream_func(api_messages, merged_cfg):
                        if self.cancel_generation:
                            yield f"data: {json.dumps({'type':'error','content':'Generation cancelled'})}\n\n"
                            return
                        full += chunk
                        yield f"data: {json.dumps({'type':'content','content':chunk})}\n\n"
                        time.sleep(0.005)

                    if not self.temporary_mode and not self.incognito_mode:
                        self.chat_manager.add_message("assistant", full, model)

                    yield f"data: {json.dumps({'type':'complete'})}\n\n"
                except Exception as e:
                    err = f"Error: {e}"
                    yield f"data: {json.dumps({'type':'error','content':err})}\n\n"
            return Response(generate(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'})

        # ---- Context documents (binary-safe) ----
        @app.route('/api/context/docs', methods=['GET'])
        def context_list_route():
            return jsonify({"documents": list_documents()})

        @app.route('/api/context/docs', methods=['POST'])
        def context_upload_route():
            if 'file' in request.files:
                f = request.files['file']
                ok, meta = save_document(f.filename, f.read())
                return (jsonify({"success": ok, "document": meta}), 200 if ok else 400)
            data = request.get_json() or {}
            name = data.get('name') or 'pasted.txt'
            content = data.get('content') or ''
            ok, meta = save_document(name, content)
            return (jsonify({"success": ok, "document": meta}), 200 if ok else 400)

        @app.route('/api/context/docs/<name>', methods=['GET'])
        def context_get_route(name: str):
            doc = get_document(name)
            if not doc:
                return jsonify({"success": False, "error": "not_found"}), 404
            return jsonify({"success": True, "document": doc})

        @app.route('/api/context/docs/<name>', methods=['DELETE'])
        def context_delete_route(name: str):
            return jsonify({"success": delete_document(name)})

        @app.route('/api/context/docs/<doc_id>/full', methods=['GET'])
        def context_get_full_route(doc_id: str):
            """Get full document data including sections, highlights, text."""
            from document_db import document_db

            # Get document from database
            doc_data = document_db.get_full_document_data(doc_id)
            if not doc_data:
                return jsonify({"success": False, "error": "Document not found"}), 404

            # Get the text content
            doc_dir = context_documents.documents_dir / doc_id
            text_file = doc_dir / 'text.txt'
            text = ""
            if text_file.exists():
                text = text_file.read_text(encoding='utf-8')

            return jsonify({
                "success": True,
                "document": doc_data,
                "text": text
            })

        @app.route('/api/context/docs/<doc_id>/highlights', methods=['POST'])
        def add_highlight_route(doc_id: str):
            """Add a highlight/selection to a document."""
            data = request.get_json() or {}
            start = int(data.get('start', -1))
            end = int(data.get('end', -1))
            label = data.get('label', '')

            if start < 0 or end <= start:
                return jsonify({"success": False, "error": "Invalid start/end positions"}), 400

            hid = context_documents.add_highlight(doc_id, start, end, label)
            if hid:
                return jsonify({"success": True, "highlight_id": hid})
            return jsonify({"success": False, "error": "Failed to add highlight"}), 400

        @app.route('/api/context/docs/<doc_id>/highlights/<highlight_id>', methods=['DELETE'])
        def delete_highlight_route(doc_id: str, highlight_id: str):
            """Delete a highlight from a document."""
            success = context_documents.remove_highlight(doc_id, highlight_id)
            return jsonify({"success": success})

        @app.route('/api/context/docs/save-selection', methods=['POST'])
        def save_doc_selection_route():
            """Save selected sections and highlights for a document."""
            from document_db import document_db

            data = request.get_json() or {}
            doc_id = data.get('doc_id')
            sections = data.get('sections', [])
            highlights = data.get('highlights', [])

            if not doc_id:
                return jsonify({"success": False, "error": "No doc_id provided"}), 400

            # Clear existing selections for this document
            document_db.clear_selections(doc_id)

            # Save new selections
            for section_id in sections:
                document_db.set_selection(doc_id, 'section', str(section_id), True)

            for highlight_id in highlights:
                document_db.set_selection(doc_id, 'highlight', str(highlight_id), True)

            return jsonify({"success": True})

        # ---- Summarization ----
        @app.route('/api/chat/summarize', methods=['POST'])
        def summarize_response():
            data = request.get_json() or {}
            content = (data.get('content') or '').strip()
            if not content:
                return jsonify({"success": False, "error": "No content"}), 400

            # Use header overrides if present; fall back to current config
            provider = (data.get('provider') or self.config.get("provider"))
            model = (data.get('model') or self.config.get(f"{provider}_model") or self.config.get("model") or "")

            prompt = (
                "Summarize the following response in ~70% fewer tokens, keeping key points:\n\n"
                + content + "\n\nSummary:"
            )

            stream_func = {
                "groq": call_groq_stream,
                "google": call_google_stream,
                "mistral": call_mistral_stream,
                "openrouter": call_openrouter_stream,
                "llamacpp": call_llamacpp_stream,
            }.get(provider)

            if not stream_func:
                return jsonify({"success": False, "error": f"Unknown provider: {provider}"}), 400

            # Build a merged config that pins the chosen model (and per-model settings)
            merged_cfg = dict(self.config.config)
            if model:
                merged_cfg["model"] = model
                merged_cfg[f"{provider}_model"] = model

            # Optional: apply per-model sampling settings if present
            ms = self.config.get_model_settings(provider, model) if hasattr(self.config, "get_model_settings") else {}
            if "top_p" in ms: merged_cfg["top_p"] = ms["top_p"]
            if "top_k" in ms: merged_cfg["top_k"] = ms["top_k"]

            summary = ""
            for chunk in stream_func([{"role": "user", "content": prompt}], merged_cfg):
                summary += chunk

            return jsonify({"success": True, "summary": summary.strip()})

        # ---- llama.cpp controls ----
        @app.route('/api/llamacpp/status')
        def llamacpp_status():
            """Get llama.cpp server status and loaded model info."""
            try:
                base = self.config.get("llamacpp_url", "http://192.168.2.115:8080").rstrip("/")
                ssh_on = bool(self.config.get("llamacpp_ssh_enabled", False))
                loaded = []

                # Check /v1/models first (works for both router mode and legacy)
                try:
                    rm = requests.get(f"{base}/v1/models", timeout=5)
                    if rm.status_code == 200:
                        for m in rm.json().get("data", []):
                            mid = m.get("id", "")
                            status_val = m.get("status", {}).get("value", "") if isinstance(m.get("status"), dict) else ""
                            # Router mode: only count "ready" models as loaded
                            # Legacy mode / single-model: no status field, treat all as loaded
                            if mid and (status_val in ("ready", "") or not status_val):
                                if status_val == "unloaded" or status_val == "loading":
                                    continue
                                # In single-model mode, the id is the full path — use the
                                # configured model name for display consistency
                                display_name = mid
                                if self._single_model_mode:
                                    display_name = self.config.get("llamacpp_model", mid)
                                loaded.append({
                                    "name": display_name,
                                    "path": mid,
                                    "n_ctx": 0,
                                    "slot_id": 0,
                                    "state": status_val or "loaded"
                                })
                except Exception:
                    pass

                # If a model is reported as loaded, clear transient loading state.
                # This avoids stale UI "Loading..." state when the background load thread
                # has not finished yet but llama.cpp is already serving inference.
                if loaded and self._single_model_loading:
                    self._single_model_loading = False

                # Try to get n_ctx from /slots for loaded models
                if loaded:
                    try:
                        rs = requests.get(f"{base}/slots", timeout=3)
                        if rs.status_code == 200:
                            slots = rs.json()
                            if isinstance(slots, list) and slots:
                                loaded[0]["n_ctx"] = slots[0].get("n_ctx", 0)
                    except Exception:
                        pass

                # Attach per-model VRAM metadata (for UI labels/guards)
                for lm in loaded:
                    ms = self.config.get_model_settings("llamacpp", lm.get("name", ""))
                    vram = ms.get("vram_required_gb")
                    if isinstance(vram, (int, float)) and vram > 0:
                        lm["vram_required_gb"] = float(vram)

                # If nothing from /v1/models, check /health
                if not loaded:
                    try:
                        rh = requests.get(f"{base}/health", timeout=5)
                        if rh.status_code == 200:
                            # In single-model mode, health=200 means the model IS loaded
                            if self._single_model_mode:
                                model_name = self.config.get("llamacpp_model", "unknown")
                                n_ctx = 0
                                try:
                                    rs = requests.get(f"{base}/slots", timeout=3)
                                    if rs.status_code == 200:
                                        slots = rs.json()
                                        if isinstance(slots, list) and slots:
                                            n_ctx = slots[0].get("n_ctx", 0)
                                except Exception:
                                    pass
                                loaded.append({
                                    "name": model_name,
                                    "path": model_name,
                                    "n_ctx": n_ctx,
                                    "slot_id": 0,
                                    "state": "loaded"
                                })
                            else:
                                return jsonify({"success": True, "server_running": True, "loaded_models": [], "slots": [], "ssh_enabled": ssh_on})
                        else:
                            return jsonify({"success": False, "server_running": False, "loaded_models": [], "slots": [], "ssh_enabled": ssh_on})
                    except Exception:
                        return jsonify({"success": False, "server_running": False, "loaded_models": [], "slots": [], "ssh_enabled": ssh_on})

                response = {
                    "success": True,
                    "server_running": True,
                    "loaded_models": loaded,
                    "slots": [],
                    "ssh_enabled": ssh_on
                }

                # Surface background loading only when there is not yet a loaded model.
                if self._single_model_loading and not loaded:
                    response["loading"] = True
                    response["loading_model"] = self.config.get("llamacpp_model", "unknown")

                return jsonify(response)
            except requests.exceptions.ConnectionError:
                return jsonify({"success": False, "server_running": False, "loaded_models": [],
                                "ssh_enabled": bool(self.config.get("llamacpp_ssh_enabled", False)),
                                "message": "Cannot connect to llama.cpp server"})
            except Exception as e:
                return jsonify({"success": False, "server_running": False, "loaded_models": [],
                                "ssh_enabled": bool(self.config.get("llamacpp_ssh_enabled", False)),
                                "message": str(e)})

        @app.route('/api/llamacpp/models')
        def llamacpp_models():
            """List available models from llama.cpp server (requires --models-dir on server)."""
            try:
                base = self.config.get("llamacpp_url", "http://127.0.0.1:8080").rstrip("/")
                r = requests.get(f"{base}/v1/models", timeout=5)
                if r.status_code == 200:
                    data = r.json()
                    models = []
                    for m in data.get("data", []):
                        model_id = m.get("id", "")
                        if model_id:
                            models.append({
                                "id": model_id,
                                "object": m.get("object", "model"),
                                "owned_by": m.get("owned_by", "local")
                            })
                    return jsonify({"success": True, "models": models})
                return jsonify({"success": False, "models": [], "error": f"Server returned {r.status_code}"})
            except Exception as e:
                return jsonify({"success": False, "models": [], "error": str(e)})

        @app.route('/api/llamacpp/load', methods=['POST'])
        def llamacpp_load():
            """
            Load a model into llama.cpp server.
            Expects JSON: {"model": "model_name_or_path"}
            Tries multiple methods to load the model.
            """
            try:
                data = request.get_json() or {}
                model = data.get("model") or data.get("filename") or ""
                slot_id = data.get("slot_id", 0)
                n_ctx = int(data.get("n_ctx", 0))

                if not model:
                    return jsonify({"success": False, "error": "No model specified"}), 400

                base = self.config.get("llamacpp_url", "http://192.168.2.115:8080").rstrip("/")
                model_dir = self.config.get("llamacpp_model_dir", "/home/josh/models")

                # Guard against known VRAM over-commit before attempting load
                model_settings = self.config.get_model_settings("llamacpp", model)
                model_vram = model_settings.get("vram_required_gb")
                total_vram = float(self.config.get("llamacpp_total_vram_gb", 0) or 0)
                if isinstance(model_vram, (int, float)) and model_vram > 0 and total_vram > 0 and model_vram > total_vram:
                    return jsonify({
                        "success": False,
                        "error": (
                            f"Model requires ~{float(model_vram):.1f}GB VRAM, but configured total VRAM is "
                            f"{total_vram:.1f}GB. Lower GPU offload (ngl) to use more system RAM before loading."
                        ),
                        "vram_blocked": True,
                        "required_vram_gb": float(model_vram),
                        "total_vram_gb": total_vram,
                    }), 400

                # Check for per-model n_gpu_layers (from request body or saved settings)
                n_gpu_layers = data.get("n_gpu_layers")
                if n_gpu_layers is None:
                    ms = self.config.get_model_settings("llamacpp", model)
                    if "n_gpu_layers" in ms:
                        n_gpu_layers = int(ms["n_gpu_layers"])

                # --- Single-model mode: custom ngl requires a dedicated server ---
                if n_gpu_layers is not None and self.config.get("llamacpp_ssh_enabled"):
                    print(f"[load] Custom ngl={n_gpu_layers} for {model} — starting single-model server (async)")

                    # Set state immediately so status endpoint knows what's happening
                    self._single_model_mode = True
                    self._single_model_loading = True
                    self.config.set("llamacpp_model", model)
                    self.config.save_config()

                    # Launch the server in a background thread — don't block the request
                    def _bg_start():
                        try:
                            ok, msg = _ssh_start_single_model(self, model, n_gpu_layers, n_ctx)
                            if ok:
                                print(f"[load] Single-model server ready for {model}")
                                # Auto-detect context size
                                try:
                                    detected_ctx = get_llamacpp_context_size(self.config.config)
                                    if detected_ctx > 0:
                                        self.config.set_model_settings("llamacpp", model, {"context_window": detected_ctx})
                                        print(f"[load] Detected n_ctx={detected_ctx}")
                                except Exception:
                                    pass
                            else:
                                print(f"[load] Single-model server FAILED: {msg}")
                                self._single_model_mode = False
                        finally:
                            self._single_model_loading = False

                    threading.Thread(target=_bg_start, daemon=True).start()

                    # Return immediately — frontend will poll /api/llamacpp/status
                    return jsonify({
                        "success": True, "loading": True, "model": model,
                        "single_model_mode": True,
                        "message": "Starting single-model server in background..."
                    })

                # --- Router mode (default path: no custom ngl) ---

                def _ensure_server_up():
                    """Make sure server is reachable; auto-start via SSH if needed. Returns (ok, msg)."""
                    try:
                        requests.get(f"{base}/health", timeout=3)
                        return True, "already running"
                    except Exception:
                        if self.config.get("llamacpp_ssh_enabled"):
                            ok, msg = _ssh_start_server(self)
                            if ok:
                                self._single_model_mode = False
                            return ok, msg
                        return False, "Server offline and SSH management is disabled"

                # If switching from single-model mode back to router, force restart
                if self._single_model_mode:
                    print("[load] Switching from single-model mode back to router mode")
                    ok, msg = _ssh_start_server(self)
                    if not ok:
                        return jsonify({"success": False, "error": f"Failed to restart router: {msg}"}), 503
                    self._single_model_mode = False
                else:
                    ok, msg = _ensure_server_up()
                    if not ok:
                        return jsonify({"success": False, "error": f"Server offline: {msg}"}), 503

                # --- Router mode load (async) ---
                self._single_model_loading = True
                self.config.set("llamacpp_model", model)
                self.config.save_config()

                def _bg_router_load():
                    try:
                        # Unload any currently loaded model first to free VRAM
                        current_model = self.config.get("llamacpp_model", "")
                        if current_model and current_model != model:
                            try:
                                requests.post(f"{base}/models/unload", json={"model": current_model}, timeout=30)
                                time.sleep(1)
                            except Exception:
                                pass

                        # Load via router mode API (POST /models/load)
                        loaded = False
                        load_error = ""

                        try:
                            r = requests.post(f"{base}/models/load", json={"model": model}, timeout=30)
                            if r.status_code == 200:
                                rj = r.json()
                                if rj.get("success"):
                                    poll_timeout = 180
                                    poll_start = time.time()
                                    while time.time() - poll_start < poll_timeout:
                                        time.sleep(2)
                                        try:
                                            mr = requests.get(f"{base}/v1/models", timeout=5)
                                            if mr.status_code == 200:
                                                for m in mr.json().get("data", []):
                                                    if m.get("id") == model:
                                                        st = m.get("status", {}).get("value", "")
                                                        if st == "ready":
                                                            loaded = True
                                                            break
                                                        elif st == "unloaded":
                                                            load_error = "Model failed to load (likely OOM)"
                                                            break
                                            if loaded or load_error:
                                                break
                                        except Exception:
                                            pass
                                    if not loaded and not load_error:
                                        load_error = f"Model load timed out after {poll_timeout}s"
                                else:
                                    load_error = rj.get("error", "Unknown error")
                            else:
                                load_error = f"Server returned {r.status_code}"
                        except requests.exceptions.ConnectionError:
                            load_error = "Server crashed during load"
                        except Exception as e:
                            load_error = str(e)

                        # Fallback: legacy slots API
                        if not loaded and "crashed" not in load_error:
                            model_paths = [model, f"{model_dir}/{model}", f"{model_dir.rstrip('/')}/{model}"]
                            for path in model_paths:
                                try:
                                    payload = {"filename": path}
                                    if n_ctx > 0:
                                        payload["n_ctx"] = n_ctx
                                    r = requests.post(f"{base}/slots/{slot_id}?action=load", json=payload, timeout=180)
                                    if r.status_code == 200:
                                        loaded = True
                                        load_error = ""
                                        break
                                except requests.exceptions.ConnectionError:
                                    load_error = "Server crashed during load"
                                    break
                                except Exception:
                                    continue

                        # If server crashed, restart via SSH
                        if not loaded and "crashed" in load_error and self.config.get("llamacpp_ssh_enabled"):
                            print("llama-server crashed during load — restarting via SSH...")
                            time.sleep(1)
                            _ssh_start_server(self)

                        if loaded:
                            print(f"[load] Router mode: {model} loaded successfully")
                            try:
                                detected_ctx = get_llamacpp_context_size(self.config.config)
                                if detected_ctx > 0:
                                    self.config.set_model_settings("llamacpp", model, {"context_window": detected_ctx})
                                    print(f"[load] Detected n_ctx={detected_ctx}")
                            except Exception:
                                pass
                        else:
                            print(f"[load] Router mode FAILED: {load_error}")
                    finally:
                        self._single_model_loading = False

                threading.Thread(target=_bg_router_load, daemon=True).start()

                return jsonify({
                    "success": True, "loading": True, "model": model,
                    "message": "Loading model in background..."
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/llamacpp/unload', methods=['POST'])
        def llamacpp_unload():
            """Unload model from llama.cpp server."""
            try:
                data = request.get_json() or {}
                slot_id = data.get("slot_id", 0)
                model_name = self.config.get("llamacpp_model", "")

                base = self.config.get("llamacpp_url", "http://192.168.2.115:8080").rstrip("/")

                # Single-model mode: kill the dedicated server and restart the router
                if self._single_model_mode:
                    print("[unload] Single-model mode — killing server and restarting router")
                    ok, msg = _ssh_start_server(self)
                    if ok:
                        self._single_model_mode = False
                        return jsonify({"success": True, "unloaded": True, "router_restarted": True})
                    # Router restart failed — flag stays True so next attempt knows the state
                    return jsonify({"success": False, "error": f"Router restart failed: {msg}"}), 500

                # Method 1: Router mode /models/unload (newer llama.cpp with --models-dir)
                if model_name:
                    try:
                        ru = requests.post(f"{base}/models/unload", json={"model": model_name}, timeout=30)
                        if ru.status_code == 200 and ru.json().get("success"):
                            return jsonify({"success": True, "unloaded": True})
                    except Exception:
                        pass

                # Method 2: slots API erase (legacy)
                try:
                    r = requests.post(f"{base}/slots/{slot_id}?action=erase", timeout=30)
                    if r.status_code == 200:
                        return jsonify({"success": True, "unloaded": True})
                except Exception:
                    pass

                return jsonify({"success": False, "error": "All unload methods failed"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/llamacpp/refresh', methods=['POST'])
        def llamacpp_refresh():
            """Refresh available models by scanning the remote models directory via SSH."""
            try:
                # Primary: scan the models directory via SSH for all .gguf files
                scanned = _ssh_scan_models_dir(self)
                if scanned:
                    model_names = [m["id"] for m in scanned]
                    self.config.update_stored_models("llamacpp", model_names)
                    # Cache the full catalog for browse panel
                    self.config.set("llamacpp_catalog", scanned)
                    self.config.save_config()

                    # Auto-select first real model if current is just "model"
                    current = self.config.get("llamacpp_model", "model")
                    if current == "model" and model_names:
                        self.config.set("llamacpp_model", model_names[0])
                        self.config.save_config()

                    return jsonify({"success": True, "models": model_names, "catalog": scanned})

                # Fallback: query the running server's /v1/models
                from api_clients import get_available_llamacpp_models
                models = get_available_llamacpp_models(self.config.config)
                if models:
                    self.config.update_stored_models("llamacpp", models)
                    return jsonify({"success": True, "models": models, "note": "From server API"})

                # Return stored models if we can't fetch new ones
                stored = self.config.get("stored_llamacpp_models", [])
                return jsonify({"success": True, "models": stored, "note": "Using cached models"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e), "models": []}), 500

        # ---- llama.cpp SSH remote management ----

        def _ssh_start_server(self_ref):
            """Start llama-server on remote machine via SSH. Returns (success, message)."""
            cfg = self_ref.config
            if not cfg.get("llamacpp_ssh_enabled"):
                return False, "SSH management is disabled"

            ssh_host = cfg.get("llamacpp_ssh_host", "192.168.2.115")
            ssh_port = int(cfg.get("llamacpp_ssh_port", 2222))
            ssh_user = cfg.get("llamacpp_ssh_user", "josh")
            binary = cfg.get("llamacpp_server_binary", "~/src/llama.cpp/build/bin/llama-server")
            extra_args = cfg.get("llamacpp_server_args", "-ngl 99 -t 16")
            model_dir = cfg.get("llamacpp_model_dir", "/home/josh/models")

            # Derive --host and --port from llamacpp_url
            parsed = urlparse(cfg.get("llamacpp_url", "http://192.168.2.115:8080"))
            server_host = parsed.hostname or "0.0.0.0"
            server_port = parsed.port or 8080

            ssh_base = [
                "ssh",
                "-p", str(ssh_port),
                "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=accept-new",
                f"{ssh_user}@{ssh_host}",
            ]

            # Step 1: Kill any existing llama-server processes to free VRAM
            try:
                subprocess.run(
                    ssh_base + ["pkill -9 -f llama-server"],
                    timeout=20, capture_output=True, text=True, check=False,
                )
                time.sleep(1)  # Wait for VRAM to be released
            except Exception:
                pass  # OK if nothing to kill

            # Step 2: Start fresh instance
            start_cmd = (
                f"nohup {binary} "
                f"--host {server_host} --port {server_port} "
                f"--models-dir {model_dir} "
                f"{extra_args} "
                f"> /tmp/llama-server.log 2>&1 &"
            )

            try:
                subprocess.run(
                    ssh_base + [start_cmd],
                    timeout=20, capture_output=True, text=True, check=False,
                )
            except subprocess.TimeoutExpired:
                return False, "SSH command timed out (start)"
            except FileNotFoundError:
                return False, "ssh binary not found"
            except Exception as e:
                return False, f"SSH error: {e}"

            # Poll /health until server is ready (up to 15s)
            base = cfg.get("llamacpp_url", "http://192.168.2.115:8080").rstrip("/")
            for _ in range(30):
                time.sleep(0.5)
                try:
                    r = requests.get(f"{base}/health", timeout=2)
                    if r.status_code == 200:
                        return True, "Server started"
                except Exception:
                    pass
            return False, "Server did not become ready within 15 seconds"

        def _ssh_get_log_tail(self_ref, lines=30):
            """Fetch the last N lines of /tmp/llama-server.log via SSH."""
            cfg = self_ref.config
            try:
                ssh_host = cfg.get("llamacpp_ssh_host", "192.168.2.115")
                ssh_port = int(cfg.get("llamacpp_ssh_port", 2222))
                ssh_user = cfg.get("llamacpp_ssh_user", "josh")
                result = subprocess.run(
                    ["ssh", "-p", str(ssh_port), "-o", "ConnectTimeout=5",
                     f"{ssh_user}@{ssh_host}", f"tail -{lines} /tmp/llama-server.log"],
                    timeout=20, capture_output=True, text=True, check=False,
                )
                return result.stdout.strip() or result.stderr.strip() or "(empty log)"
            except Exception as e:
                return f"(could not fetch log: {e})"

        def _ssh_scan_models_dir(self_ref):
            """Scan the remote models directory via SSH for .gguf files/folders.
            Returns a list of dicts: [{"id": name, "size_gb": float, "path": str}, ...]
            """
            cfg = self_ref.config
            if not cfg.get("llamacpp_ssh_enabled"):
                return []

            ssh_host = cfg.get("llamacpp_ssh_host", "192.168.2.115")
            ssh_port = int(cfg.get("llamacpp_ssh_port", 2222))
            ssh_user = cfg.get("llamacpp_ssh_user", "josh")
            model_dir = cfg.get("llamacpp_model_dir", "/home/josh/models")

            ssh_base = [
                "ssh", "-p", str(ssh_port),
                "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=accept-new",
                f"{ssh_user}@{ssh_host}",
            ]
<<<<<<< codex/fix-model-search-to-display-all-models-rxkxgj
            # List ALL top-level directories and top-level .gguf files in model_dir.
            # This supports both folder-based model formats and single .gguf files.
            q_model_dir = shlex.quote(model_dir)
            scan_cmd = (
                f"cd {q_model_dir} 2>/dev/null || exit 1; "
                f"find . -mindepth 1 -maxdepth 1 \\( "
                f" -type d -o -type f -name '*.gguf' \\) | while IFS= read -r entry; do "
                f"  name=${{entry#./}}; "
                f"  if [ -d \"$entry\" ]; then "
                f"    size=$(du -sb \"$entry\" 2>/dev/null | cut -f1); "
                f"    [ -n \"$size\" ] || size=$(du -sk \"$entry\" 2>/dev/null | awk '{{print $1 * 1024}}'); "
                f"    [ -n \"$size\" ] || size=0; "
                f"    printf '%s\\t%s\\tdir\\n' \"$name\" \"$size\"; "
                f"  elif [ -f \"$entry\" ]; then "
                f"    size=$(wc -c < \"$entry\" 2>/dev/null); "
                f"    [ -n \"$size\" ] || size=0; "
                f"    printf '%s\\t%s\\tfile\\n' \"$name\" \"$size\"; "
                f"  fi; "
=======

            # List ALL top-level directories and top-level .gguf files in model_dir.
            # Some model formats are stored as folders (e.g. safetensors) with no top-level .gguf.
            scan_cmd = (
                f"cd {model_dir} 2>/dev/null && "
                f"for d in */; do "
                f"  [ -d \"$d\" ] || continue; "
                f"  size=$(du -sb \"$d\" 2>/dev/null | cut -f1); "
                f"  [ -n \"$size\" ] || size=0; "
                f"  echo \"${{d%/}}\\t$size\\tdir\"; "
                f"done; "
                f"for f in *.gguf; do "
                f"  [ -f \"$f\" ] && stat --printf='%n\\t%s\\tfile\\n' \"$f\" 2>/dev/null; "
>>>>>>> local_model_catalog
                f"done"
            )

            try:
                result = subprocess.run(
                    ssh_base + [scan_cmd],
                    timeout=45, capture_output=True, text=True, check=False,
                )
                models = []
                for line in result.stdout.strip().splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        try:
                            size_bytes = int(parts[1])
                            size_gb = round(size_bytes / (1024**3), 1)
                        except (ValueError, IndexError):
                            size_gb = 0
                        if name:
                            models.append({
                                "id": name,
                                "size_gb": size_gb,
                                "path": f"{model_dir}/{name}",
                            })
                return models
            except Exception as e:
                print(f"SSH scan models dir failed: {e}")
                return []

        def _ssh_start_single_model(self_ref, model_path, n_gpu_layers, n_ctx):
            """Start a dedicated single-model llama-server via SSH with custom ngl. Returns (success, message)."""
            cfg = self_ref.config
            if not cfg.get("llamacpp_ssh_enabled"):
                return False, "SSH management is disabled"

            ssh_host = cfg.get("llamacpp_ssh_host", "192.168.2.115")
            ssh_port = int(cfg.get("llamacpp_ssh_port", 2222))
            ssh_user = cfg.get("llamacpp_ssh_user", "josh")
            binary = cfg.get("llamacpp_server_binary", "~/src/llama.cpp/build/bin/llama-server")
            model_dir = cfg.get("llamacpp_model_dir", "/home/josh/models")

            parsed = urlparse(cfg.get("llamacpp_url", "http://192.168.2.115:8080"))
            server_host = parsed.hostname or "0.0.0.0"
            server_port = parsed.port or 8080

            # Build the full model path
            if not model_path.startswith("/"):
                model_path = f"{model_dir.rstrip('/')}/{model_path}"

            ssh_base = [
                "ssh",
                "-p", str(ssh_port),
                "-o", "ConnectTimeout=5",
                "-o", "StrictHostKeyChecking=accept-new",
                f"{ssh_user}@{ssh_host}",
            ]

            # Resolve model path: if it's a directory, find the .gguf file inside
            try:
                resolve_cmd = (
                    f'if [ -d "{model_path}" ]; then '
                    f'  find "{model_path}" -maxdepth 1 -name "*.gguf" -type f | head -1; '
                    f'elif [ -f "{model_path}" ]; then '
                    f'  echo "{model_path}"; '
                    f'elif [ -f "{model_path}.gguf" ]; then '
                    f'  echo "{model_path}.gguf"; '
                    f'else '
                    f'  echo ""; '
                    f'fi'
                )
                result = subprocess.run(
                    ssh_base + [resolve_cmd],
                    timeout=20, capture_output=True, text=True, check=False,
                )
                resolved = result.stdout.strip()
                if resolved:
                    print(f"[single-model] Resolved model path: {model_path} -> {resolved}")
                    model_path = resolved
                else:
                    return False, f"Model not found at {model_path} (no .gguf file)"
            except Exception as e:
                print(f"[single-model] Warning: could not resolve model path: {e}")

            # Step 1: Kill any existing llama-server processes
            try:
                subprocess.run(
                    ssh_base + ["pkill -9 -f llama-server"],
                    timeout=20, capture_output=True, text=True, check=False,
                )
                time.sleep(1)
            except Exception:
                pass

            # Step 2: Start single-model server with custom ngl
            ctx_flag = f"-c {n_ctx}" if n_ctx and n_ctx > 0 else ""
            start_cmd = (
                f"nohup {binary} "
                f"-m {model_path} "
                f"-ngl {n_gpu_layers} "
                f"{ctx_flag} "
                f"-t 16 "
                f"--host {server_host} --port {server_port} "
                f"> /tmp/llama-server.log 2>&1 &"
            )
            print(f"[single-model] SSH cmd: {start_cmd}")

            try:
                subprocess.run(
                    ssh_base + [start_cmd],
                    timeout=20, capture_output=True, text=True, check=False,
                )
            except subprocess.TimeoutExpired:
                return False, "SSH command timed out"
            except FileNotFoundError:
                return False, "ssh binary not found"
            except Exception as e:
                return False, f"SSH error: {e}"

            # Poll /health until server reports status "ok" (up to 180s for large models)
            # Just checking status_code == 200 is not enough — there's a brief window
            # after the HTTP listener binds but before model loading starts where
            # /health may return 200. We must check the response body too.
            base = cfg.get("llamacpp_url", "http://192.168.2.115:8080").rstrip("/")
            health_seen = False
            for i in range(360):
                time.sleep(0.5)
                try:
                    r = requests.get(f"{base}/health", timeout=2)
                    if r.status_code == 200:
                        # Verify the body says "ok" (not just an early 200)
                        try:
                            body = r.json()
                            status = body.get("status", "")
                            if status == "ok":
                                # Double-check: verify /v1/models has a model listed
                                try:
                                    mr = requests.get(f"{base}/v1/models", timeout=3)
                                    if mr.status_code == 200 and mr.json().get("data"):
                                        print(f"[single-model] Model ready after {i*0.5:.0f}s")
                                        return True, "Single-model server started"
                                except Exception:
                                    pass
                                # /v1/models not available yet, but health is ok — accept it
                                print(f"[single-model] Health ok after {i*0.5:.0f}s (models endpoint not ready)")
                                return True, "Single-model server started"
                            elif status == "loading":
                                if not health_seen:
                                    progress = body.get("progress", 0)
                                    print(f"[single-model] Model loading... progress={progress}")
                                    health_seen = True
                                continue
                        except (ValueError, KeyError):
                            # Non-JSON 200 — could be an early response, keep polling
                            continue
                    elif r.status_code == 503:
                        # 503 = loading, keep polling
                        health_seen = True
                        continue
                except requests.exceptions.ConnectionError:
                    if health_seen:
                        # Server was up but now connection refused — it crashed (likely OOM)
                        return False, "Server crashed during model loading (likely OOM)"
                except Exception:
                    pass
            return False, "Single-model server did not become ready within 180 seconds"

        @app.route('/api/llamacpp/server/log')
        def llamacpp_server_log():
            """Fetch the last lines of the remote llama-server log."""
            lines = request.args.get("lines", 50, type=int)
            log = _ssh_get_log_tail(self, lines)
            return jsonify({"success": True, "log": log})

        @app.route('/api/llamacpp/server/start', methods=['POST'])
        def llamacpp_server_start():
            """Start llama-server on remote machine via SSH (async)."""
            self._single_model_loading = True  # Reuse loading flag to show progress

            def _bg_start_server():
                try:
                    ok, msg = _ssh_start_server(self)
                    if ok:
                        print(f"[server] Router started: {msg}")
                    else:
                        print(f"[server] Router start FAILED: {msg}")
                finally:
                    self._single_model_loading = False

            threading.Thread(target=_bg_start_server, daemon=True).start()
            return jsonify({"success": True, "starting": True, "message": "Starting server in background..."})

        @app.route('/api/llamacpp/server/stop', methods=['POST'])
        def llamacpp_server_stop():
            """Stop llama-server on remote machine via SSH."""
            cfg = self.config
            if not cfg.get("llamacpp_ssh_enabled"):
                return jsonify({"success": False, "error": "SSH management is disabled"}), 400

            ssh_host = cfg.get("llamacpp_ssh_host", "192.168.2.115")
            ssh_port = int(cfg.get("llamacpp_ssh_port", 2222))
            ssh_user = cfg.get("llamacpp_ssh_user", "josh")

            ssh_cmd = [
                "ssh",
                "-p", str(ssh_port),
                "-o", "ConnectTimeout=5",
                f"{ssh_user}@{ssh_host}",
                "pkill -f llama-server",
            ]

            try:
                result = subprocess.run(ssh_cmd, timeout=10, capture_output=True, text=True, check=False)
                # pkill returns 0 if processes were killed, 1 if none found
                return jsonify({"success": True, "stopped": True, "returncode": result.returncode})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/llamacpp/ssh-config', methods=['GET'])
        def llamacpp_ssh_config_get():
            """Get SSH management config."""
            return jsonify({
                "success": True,
                "llamacpp_ssh_enabled": bool(self.config.get("llamacpp_ssh_enabled", False)),
                "llamacpp_ssh_host": self.config.get("llamacpp_ssh_host", ""),
                "llamacpp_ssh_port": int(self.config.get("llamacpp_ssh_port", 22)),
                "llamacpp_ssh_user": self.config.get("llamacpp_ssh_user", ""),
                "llamacpp_server_binary": self.config.get("llamacpp_server_binary", ""),
                "llamacpp_server_args": self.config.get("llamacpp_server_args", ""),
                "llamacpp_total_vram_gb": float(self.config.get("llamacpp_total_vram_gb", 0) or 0),
            })

        @app.route('/api/llamacpp/ssh-config', methods=['POST'])
        def llamacpp_ssh_config_set():
            """Save SSH management config."""
            data = request.get_json() or {}
            allowed = {
                "llamacpp_ssh_enabled", "llamacpp_ssh_host", "llamacpp_ssh_port",
                "llamacpp_ssh_user", "llamacpp_server_binary", "llamacpp_server_args", "llamacpp_total_vram_gb",
            }
            for key in allowed:
                if key in data:
                    val = data[key]
                    if key == "llamacpp_ssh_port":
                        val = int(val)
                    elif key == "llamacpp_ssh_enabled":
                        val = bool(val)
                    elif key == "llamacpp_total_vram_gb":
                        val = max(0.0, float(val or 0))
                    self.config.set(key, val)
            self.config.save_config()
            return jsonify({"success": True})

        # --- Context: token summary (used by the context bar) ---
        @app.route('/api/context/token-summary')
        def context_token_summary():
            try:
                counts = context_documents.get_context_token_count()
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

            chat_tok = self.chat_manager.current_chat.get("token_count", 0)
            docs_tok = int(counts.get("total_tokens", 0))
            total = chat_tok + docs_tok  # prompt/system counted as 0 here (simple view)

            provider = self.config.get("provider", "groq")
            provider_max = int(self.config.get(f"{provider}_max_tokens", 4096))

            return jsonify({
                "success": True,
                "breakdown": {"chat": chat_tok, "docs": docs_tok, "prompt": 0, "total": total},
                "limits": {"provider_max": provider_max},
                "doc_count": counts.get("doc_count", 0),
                "doc_tokens": counts.get("doc_tokens", {}),
                "total_tokens": total
            })

        # --- Context: upload alias to match /api/context/docs/upload ---
        @app.route('/api/context/docs/upload', methods=['POST'])
        def context_upload_alias():
            if 'file' not in request.files:
                return jsonify({"success": False, "error": "file required"}), 400
            f = request.files['file']
            ok, meta = save_document(f.filename, f.read())
            return (jsonify({"success": ok, "document": meta}), 200 if ok else 400)

        # --- Context: select/deselect a document ---
        @app.route('/api/context/docs/select', methods=['POST'])
        def context_select_route():
            data = request.get_json() or {}
            doc_id = data.get('doc_id')
            selected = bool(data.get('selected'))
            if not doc_id:
                return jsonify({"success": False, "error": "doc_id required"}), 400
            ok = context_documents.update_document_selection(doc_id, selected)
            return (jsonify({"success": ok}), 200 if ok else 400)

        # --- Chat: rename (used by sidebar context menu) ---
        @app.route('/api/chat/rename', methods=['POST'])
        def rename_chat_route():
            data = request.get_json() or {}
            filename = data.get('filename') or self.chat_manager.current_chat_file
            new_title = (data.get('new_title') or '').strip()
            if not filename or not new_title:
                return jsonify({"success": False, "error": "filename and new_title required"}), 400

            safe = re.sub(r"[^\w\s-]", "", new_title)
            safe = re.sub(r"[-\s]+", "_", safe).strip("_")
            base = os.path.splitext(filename)[0]
            m = re.search(r"_(\d{8}_\d{6})$", base)
            ts = m.group(1) if m else datetime.now().strftime("%Y%m%d_%H%M%S")
            new_fn = f"{safe}_{ts}.json"

            old_path = os.path.join(self.chat_manager.chats_directory, filename)
            new_path = os.path.join(self.chat_manager.chats_directory, new_fn)
            try:
                # Load the chat file, update the title, and save it
                if os.path.exists(old_path):
                    with open(old_path, "r", encoding="utf-8") as f:
                        chat_data = json.load(f)
                    
                    # Update the title in the chat data but preserve chat_id fields
                    if isinstance(chat_data, dict):
                        chat_data["title"] = new_title
                        # Ensure chat_id fields exist (for backward compatibility)
                        if "chat_id" not in chat_data:
                            chat_data["chat_id"] = str(uuid.uuid4())
                        if "root_chat_id" not in chat_data:
                            chat_data["root_chat_id"] = chat_data["chat_id"]
                        if "parent_chat_id" not in chat_data:
                            chat_data["parent_chat_id"] = ""
                    else:
                        # If it's an old format (list), convert to dict format
                        chat_data = {
                            "chat_id": str(uuid.uuid4()),
                            "root_chat_id": str(uuid.uuid4()),
                            "parent_chat_id": "",
                            "chat_history": chat_data,
                            "title": new_title,
                            "conversation_summary": "",
                            "token_count": sum(estimate_tokens(m.get("content", "")) for m in chat_data) if isinstance(chat_data, list) else 0
                        }
                    
                    # Write the updated data to the new file
                    with open(new_path, "w", encoding="utf-8") as f:
                        json.dump(chat_data, f, indent=2, ensure_ascii=False)
                    
                    # Remove the old file
                    os.remove(old_path)
                else:
                    # If file doesn't exist, just rename it
                    os.rename(old_path, new_path)
                
                if self.chat_manager.current_chat_file == filename:
                    self.chat_manager.current_chat_file = new_fn
                    # Also update the title in the current chat if it's loaded
                    if isinstance(self.chat_manager.current_chat, dict):
                        self.chat_manager.current_chat["title"] = new_title
                
                return jsonify({"success": True, "filename": new_fn, "title": new_title})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # --- Chat: add a saved summary to a message ---
        @app.route('/api/chat/add_summary', methods=['POST'])
        def add_summary_route():
            data = request.get_json() or {}
            idx = int(data.get('message_index', -1))
            summary = data.get('summary') or ''
            summary_model = data.get('summary_model')  # <— add this
            ok = self.chat_manager.add_summary(idx, summary, summary_model)  # <— pass it
            return (jsonify({"success": ok}), 200 if ok else 400)

        # --- Chat: delete a single message by index ---
        @app.route('/api/chat/delete_message', methods=['DELETE'])
        def delete_message_route():
            data = request.get_json() or {}
            filename = data.get('chat_id') or self.chat_manager.current_chat_file
            index = int(data.get('message_index', -1))
            if not filename or index < 0:
                return jsonify({"success": False, "error": "chat_id and message_index required"}), 400
            if self.chat_manager.current_chat_file != filename:
                self.chat_manager.load_chat(filename)
            msgs = self.chat_manager.current_chat.get("chat_history", [])
            if 0 <= index < len(msgs):
                msgs.pop(index)
                self.chat_manager.current_chat["chat_history"] = msgs
                self.chat_manager.save_current_chat(force_save=True)
                return jsonify({"success": True})
            return jsonify({"success": False, "error": "index out of range"}), 400

        # --- Chat: create a side chat (branch) ---
        @app.route('/api/chat/create_side_chat', methods=['POST'])
        def create_side_chat_route():
            data = request.get_json() or {}
            # Add debug logging as requested
            print(f"DEBUG: create_side_chat received data: {data}")
            print(f"DEBUG: current_chat_file from chat_manager: {self.chat_manager.current_chat_file}")
            parent = data.get('parent_chat_id') or self.chat_manager.current_chat_file
            idx = int(data.get('parent_message_index', -1))
            selected_text = data.get('selected_text')
            # Add debug info as requested
            print(f"DEBUG: parent chat determined as: {parent}")
            print(f"DEBUG: parent_message_index: {idx}")
            if not parent or idx < 0:
                return jsonify({"success": False, "error": "parent_chat_id and parent_message_index required"}), 400

            # Load the parent chat to get its chat_id
            # Add debug info for chat loading check as requested
            print(f"DEBUG: Checking if need to load parent chat...")
            print(f"DEBUG: current_chat_file: '{self.chat_manager.current_chat_file}'")
            print(f"DEBUG: parent: '{parent}'")
            print(f"DEBUG: Are they different? {self.chat_manager.current_chat_file != parent}")
            if self.chat_manager.current_chat_file != parent:
                self.chat_manager.load_chat(parent)
            
            # Get parent chat_id and root_chat_id
            parent_chat_id = self.chat_manager.current_chat.get("chat_id", parent)
            root_chat_id = self.chat_manager.current_chat.get("root_chat_id", parent_chat_id)

            # Create a new empty side_msgs list
            side_msgs = []

            # If the request JSON has selected_text, then add a single system message with content
            if selected_text:
                side_msgs.append({
                    "role": "system",
                    "content": f"(Starting from selected text)\n\n{selected_text}",
                    "timestamp": datetime.now().strftime("%H:%M")
                })
            else:
                # Else if it's branching from a whole message, add a system message with content
                parent_msgs = list(self.chat_manager.current_chat.get("chat_history", []))
                if 0 <= idx < len(parent_msgs):
                    message_content = parent_msgs[idx].get("content", "")
                    side_msgs.append({
                        "role": "system",
                        "content": f"(Starting from selected response)\n\n{message_content}",
                        "timestamp": datetime.now().strftime("%H:%M")
                    })

            # Generate a unique ID for the side chat
            side_chat_id = str(uuid.uuid4())
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            side_fn = f"side_{side_chat_id}_{ts}.json"
            path = os.path.join(self.chat_manager.chats_directory, side_fn)

            # Add a field title to the saved JSON, initialize title as ""
            data_obj = {
                "chat_id": side_chat_id,
                "chat_history": side_msgs,
                "conversation_summary": "",
                "token_count": sum(estimate_tokens(m.get("content","")) for m in side_msgs),
                "parent_chat_id": parent_chat_id,
                "root_chat_id": root_chat_id,
                "title": ""  # Initialize title as empty string
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data_obj, f, indent=2, ensure_ascii=False)

            return jsonify({"success": True, "side_chat_id": f"side_{side_chat_id}", "filename": side_fn})

        # --- Chat: cancel generation (used by the red Cancel button) ---
        @app.route('/api/chat/cancel', methods=['POST'])
        def cancel_route():
            self.cancel_generation = True
            return jsonify({"success": True})

        # --- System Prompts Management ---
        @app.route('/api/prompts', methods=['GET'])
        def get_prompts():
            """Get all system prompts from prompts.jsonl"""
            try:
                prompts_file = os.path.join(os.path.dirname(__file__), 'prompts', 'prompts.jsonl')
                prompts = []
                
                if os.path.exists(prompts_file):
                    with open(prompts_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if line:
                                try:
                                    prompt = json.loads(line)
                                    prompts.append(prompt)
                                except json.JSONDecodeError:
                                    continue
                
                return jsonify({"success": True, "prompts": prompts})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/prompts', methods=['POST'])
        def create_prompt():
            """Create a new system prompt"""
            try:
                data = request.get_json() or {}
                prompt_id = data.get('id', '').strip()
                title = data.get('title', '').strip()
                body = data.get('body', '').strip()

                if not prompt_id or not title or not body:
                    return jsonify({"success": False, "error": "id, title, and body are required"}), 400

                # Validate ID format
                if not re.match(r'^[a-z0-9_]+$', prompt_id):
                    return jsonify({"success": False, "error": "ID must be lowercase letters, numbers, and underscores only"}), 400

                prompts_file = os.path.join(os.path.dirname(__file__), 'prompts', 'prompts.jsonl')
                
                # Check if ID already exists
                if os.path.exists(prompts_file):
                    with open(prompts_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            try:
                                existing = json.loads(line.strip())
                                if existing.get('id') == prompt_id:
                                    return jsonify({"success": False, "error": "A prompt with this ID already exists"}), 400
                            except:
                                continue

                # Append new prompt
                new_prompt = {"id": prompt_id, "title": title, "body": body}
                with open(prompts_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(new_prompt, ensure_ascii=False) + '\n')

                return jsonify({"success": True, "prompt": new_prompt})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/prompts/<prompt_id>', methods=['PUT'])
        def update_prompt(prompt_id):
            """Update an existing system prompt"""
            try:
                data = request.get_json() or {}
                title = data.get('title', '').strip()
                body = data.get('body', '').strip()

                if not title or not body:
                    return jsonify({"success": False, "error": "title and body are required"}), 400

                prompts_file = os.path.join(os.path.dirname(__file__), 'prompts', 'prompts.jsonl')
                
                if not os.path.exists(prompts_file):
                    return jsonify({"success": False, "error": "Prompts file not found"}), 404

                # Read all prompts
                prompts = []
                found = False
                with open(prompts_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            prompt = json.loads(line.strip())
                            if prompt.get('id') == prompt_id:
                                prompt['title'] = title
                                prompt['body'] = body
                                found = True
                            prompts.append(prompt)
                        except:
                            continue

                if not found:
                    return jsonify({"success": False, "error": "Prompt not found"}), 404

                # Write back all prompts
                with open(prompts_file, 'w', encoding='utf-8') as f:
                    for prompt in prompts:
                        f.write(json.dumps(prompt, ensure_ascii=False) + '\n')

                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/prompts/<prompt_id>', methods=['DELETE'])
        def delete_prompt(prompt_id):
            """Delete a system prompt"""
            try:
                prompts_file = os.path.join(os.path.dirname(__file__), 'prompts', 'prompts.jsonl')
                
                if not os.path.exists(prompts_file):
                    return jsonify({"success": False, "error": "Prompts file not found"}), 404

                # Read all prompts except the one to delete
                prompts = []
                found = False
                with open(prompts_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        try:
                            prompt = json.loads(line.strip())
                            if prompt.get('id') == prompt_id:
                                found = True
                                continue  # Skip this one
                            prompts.append(prompt)
                        except:
                            continue

                if not found:
                    return jsonify({"success": False, "error": "Prompt not found"}), 404

                # Write back remaining prompts
                with open(prompts_file, 'w', encoding='utf-8') as f:
                    for prompt in prompts:
                        f.write(json.dumps(prompt, ensure_ascii=False) + '\n')

                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    
    
    # --------------- helpers ---------------
    def get_provider_models(self, provider: str) -> List[str]:
        # Highest priority: user-defined custom list (if present)
        custom = self.config.get(f"custom_{provider}_models", [])
        if custom:
            return custom

        # Use ConfigManager's provider lists (already cached/stored)
        return self.config.get_models_for_provider(provider)

    def _stop_remote_llama_server(self):
        """Kill llama-server on the remote machine via SSH (best-effort on shutdown)."""
        try:
            if not self.config.get("llamacpp_ssh_enabled"):
                return
            ssh_host = self.config.get("llamacpp_ssh_host", "")
            ssh_port = int(self.config.get("llamacpp_ssh_port", 2222))
            ssh_user = self.config.get("llamacpp_ssh_user", "")
            if not ssh_host or not ssh_user:
                return
            print("Stopping remote llama-server via SSH...")
            subprocess.run(
                ["ssh", "-p", str(ssh_port), "-o", "ConnectTimeout=5",
                 f"{ssh_user}@{ssh_host}", "pkill -f llama-server"],
                timeout=10, capture_output=True, check=False,
            )
            print("Remote llama-server stopped.")
        except Exception as e:
            print(f"Warning: failed to stop remote llama-server: {e}")

    # --------------- run ---------------
    def run(self, host='127.0.0.1', port=5000, debug=False, standalone=True):
        if debug:
            self.app.run(host=host, port=port, debug=True)
            return
        if standalone and WEBVIEW_AVAILABLE:
            return self.run_standalone(host, port)
        return self.run_browser(host, port)

    def run_standalone(self, host='127.0.0.1', port=5000):
        print("Starting AI Chat App in standalone window...")
        from werkzeug.serving import make_server
        self.server = make_server(host, port, self.app, threaded=True)
        t = threading.Thread(target=self.server.serve_forever, daemon=True)
        t.start()
        time.sleep(1.5)
        try:
            # Best-effort screen sizing
            try:
                import tkinter as tk
                r = tk.Tk(); w, h = r.winfo_screenwidth(), r.winfo_screenheight(); r.destroy()
            except Exception:
                w, h = 1400, 900
            window_w = min(1400, int(w * 0.85))
            window_h = min(900, int(h * 0.85))
            webview.create_window('ThreadBear', f'http://{host}:{port}', width=window_w, height=window_h, resizable=True, fullscreen=False, min_size=(800, 600))
            print("Starting webview...")
            webview.start(debug=False)
        finally:
            self._stop_remote_llama_server()
            if self.server:
                print("Shutting down server...")
                self.server.shutdown()

    def run_browser(self, host='127.0.0.1', port=5000):
        from werkzeug.serving import make_server
        self.server = make_server(host, port, self.app, threaded=True)
        def _open():
            time.sleep(1)
            webbrowser.open(f'http://{host}:{port}')
        threading.Thread(target=_open, daemon=True).start()
        print(f"AI Chat App running on http://{host}:{port}")
        print("Press Ctrl+C to stop the server")
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            self._stop_remote_llama_server()
            self.server.shutdown()


def main():
    app = FlaskChatApp()
    for p in [5000, 5001, 5002, 5003]:
        try:
            app.run(port=p, standalone=True)
            break
        except Exception as e:
            print(f"Port {p} failed: {e}")
            continue

if __name__ == '__main__':
    main()
