"""
Flask-based AI Chat Application (stable routes, binary-safe uploads, Ollama load/unload with polling)
"""
from __future__ import annotations
import os
import json
import threading
import time
import re
import uuid
from datetime import datetime
from typing import Dict, List
import requests
from api_clients import estimate_tokens, get_available_ollama_models
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

# Global session for Ollama calls
session = requests.Session()
session.trust_env = False  # ignore HTTP(S)_PROXY for localhost

# App modules
from config_manager import ConfigManager
from chat_manager import ChatManager
from api_clients import (
    call_ollama_stream, call_groq_stream, call_google_stream,
    call_mistral_stream, call_openrouter_stream, call_llamacpp_stream,
    start_ollama_server,
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
        self.available_providers = ["ollama", "groq", "google", "mistral", "openrouter", "llamacpp"]
        self.last_renamed_chat = None

        self.pending_messages: Dict[int, Dict[str, str]] = {}

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
                        "ollama": call_ollama_stream,
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
                "ollama": call_ollama_stream,
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

        # ---- Ollama controls ----
        @app.route('/api/ollama/status')
        def ollama_status():
            try:
                base = self.config.get("ollama_url", "http://127.0.0.1:11434")
                r = session.get(f"{base}/api/ps", timeout=5)
                if r.status_code != 200:
                    return jsonify({"success": False, "server_running": False, "loaded_models": []})
                data = r.json()
                loaded = []
                for m in data.get('models', []):
                    size_gb = (m.get('size', 0) or 0) / (1024**3)
                    loaded.append({
                        'name': m.get('name', 'unknown'),
                        'size_bytes': m.get('size', 0) or 0,
                        'size_gb': round(size_gb, 2),
                        'digest': m.get('digest', ''),
                        'expires_at': m.get('expires_at', ''),
                    })
                return jsonify({"success": True, "server_running": True, "loaded_models": loaded, "total_memory_gb": round(sum(m['size_gb'] for m in loaded), 2)})
            except Exception as e:
                return jsonify({"success": False, "server_running": False, "loaded_models": [], "message": str(e)})

        @app.route('/api/ollama/load/<model_name>', methods=['POST'])
        def ollama_load(model_name):
            try:
                base = self.config.get("ollama_url", "http://localhost:11434")
                # Ask Ollama to keep this model loaded
                session.post(
                    f"{base}/api/generate",
                    json={"model": model_name, "prompt": " ", "keep_alive": -1},
                    timeout=30
                )
                # Query Ollama for loaded models
                r = session.get(f"{base}/api/ps", timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    size = None
                    for m in data.get("models", []):
                        if m.get("model") == model_name:
                            size = m.get("size")
                    return {"success": True, "loaded": True, "size": size}
                return {"success": True, "loaded": True, "size": None}
            except Exception as e:
                return {"success": False, "error": str(e)}, 500

        @app.route('/api/ollama/unload/<model_name>', methods=['POST'])
        def ollama_unload(model_name):
            try:
                base = self.config.get("ollama_url", "http://localhost:11434")
                # Tell Ollama to unload this model
                session.post(
                    f"{base}/api/generate",
                    json={"model": model_name, "prompt": " ", "keep_alive": 0},
                    timeout=30
                )
                # Query Ollama again to verify unload
                r = session.get(f"{base}/api/ps", timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    still_loaded = any(m.get("model") == model_name for m in data.get("models", []))
                    return {"success": True, "loaded": still_loaded}
                return {"success": True, "loaded": False}
            except Exception as e:
                return {"success": False, "error": str(e)}, 500

        @app.route('/api/ollama/refresh', methods=['POST'])
        def ollama_refresh():
            """
            Query local Ollama server for installed models and save them into config.json
            under 'stored_ollama_models'.
            """
            try:
                models = get_available_ollama_models(self.config.config)
                if not isinstance(models, list):
                    models = []

                # Persist to config.json
                self.config.update_stored_models("ollama", models)

                # Reset current model if invalid
                if self.config.get("provider") == "ollama":
                    current = self.config.get("ollama_model", "")
                    if current not in models and models:
                        self.config.set("ollama_model", models[0])

                return jsonify({"success": True, "models": models})
            except Exception as e:
                return jsonify({
                    "success": False,
                    "error": f"Failed to refresh Ollama models: {e}",
                    "models": self.config.get("stored_ollama_models", [])
                }), 500

        # ---- llama.cpp controls ----
        @app.route('/api/llamacpp/status')
        def llamacpp_status():
            """Get llama.cpp server status and loaded model info via /slots endpoint."""
            try:
                base = self.config.get("llamacpp_url", "http://127.0.0.1:8080").rstrip("/")
                r = requests.get(f"{base}/slots", timeout=5)
                if r.status_code != 200:
                    # Try /health as fallback
                    rh = requests.get(f"{base}/health", timeout=5)
                    if rh.status_code == 200:
                        return jsonify({"success": True, "server_running": True, "loaded_models": [], "slots": []})
                    return jsonify({"success": False, "server_running": False, "loaded_models": [], "slots": []})

                slots = r.json()
                if not isinstance(slots, list):
                    slots = [slots] if slots else []

                loaded = []
                for slot in slots:
                    model_path = slot.get("model", "") or slot.get("model_path", "")
                    if model_path:
                        # Extract just the filename from path
                        model_name = model_path.split("/")[-1].split("\\")[-1]
                        n_ctx = slot.get("n_ctx", 0)
                        loaded.append({
                            "name": model_name,
                            "path": model_path,
                            "n_ctx": n_ctx,
                            "slot_id": slot.get("id", 0),
                            "state": slot.get("state", "unknown")
                        })

                return jsonify({
                    "success": True,
                    "server_running": True,
                    "loaded_models": loaded,
                    "slots": slots
                })
            except requests.exceptions.ConnectionError:
                return jsonify({"success": False, "server_running": False, "loaded_models": [], "message": "Cannot connect to llama.cpp server"})
            except Exception as e:
                return jsonify({"success": False, "server_running": False, "loaded_models": [], "message": str(e)})

        @app.route('/api/llamacpp/models')
        def llamacpp_models():
            """List available models from llama.cpp server (requires --model-store on server)."""
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

                if not model:
                    return jsonify({"success": False, "error": "No model specified"}), 400

                base = self.config.get("llamacpp_url", "http://127.0.0.1:8080").rstrip("/")
                model_dir = self.config.get("llamacpp_model_dir", "/home/josh/models")

                # Build possible paths to try
                model_paths = [
                    model,  # As-is (might be full path or just filename)
                    f"{model_dir}/{model}",  # With configured model directory
                    f"{model_dir.rstrip('/')}/{model}",  # Normalized
                ]

                # Method 1: Try slots API with different path formats
                for path in model_paths:
                    try:
                        payload = {"filename": path}
                        r = requests.post(f"{base}/slots/{slot_id}?action=load", json=payload, timeout=180)
                        if r.status_code == 200:
                            # Update config with the loaded model
                            self.config.set("llamacpp_model", model)
                            self.config.save_config()
                            return jsonify({"success": True, "loaded": True, "model": model, "path": path})
                    except:
                        continue

                # Method 2: Try v1/chat/completions (auto-loads if using --model-store)
                try:
                    r2 = requests.post(
                        f"{base}/v1/chat/completions",
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1
                        },
                        timeout=180
                    )
                    if r2.status_code == 200:
                        self.config.set("llamacpp_model", model)
                        self.config.save_config()
                        return jsonify({"success": True, "loaded": True, "model": model})
                except:
                    pass

                return jsonify({
                    "success": False,
                    "error": "Failed to load model. Make sure server was started with --model-store /path/to/models/"
                })
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/llamacpp/unload', methods=['POST'])
        def llamacpp_unload():
            """Unload model from llama.cpp server slot."""
            try:
                data = request.get_json() or {}
                slot_id = data.get("slot_id", 0)

                base = self.config.get("llamacpp_url", "http://127.0.0.1:8080").rstrip("/")

                # Use slots API to unload
                r = requests.post(f"{base}/slots/{slot_id}?action=erase", timeout=30)

                if r.status_code == 200:
                    return jsonify({"success": True, "unloaded": True})

                # Some versions use different endpoint
                r2 = requests.post(f"{base}/slots/{slot_id}", json={"action": "erase"}, timeout=30)
                if r2.status_code == 200:
                    return jsonify({"success": True, "unloaded": True})

                return jsonify({"success": False, "error": f"Server returned {r.status_code}"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/llamacpp/refresh', methods=['POST'])
        def llamacpp_refresh():
            """Refresh available models from llama.cpp server."""
            try:
                from api_clients import get_available_llamacpp_models
                models = get_available_llamacpp_models(self.config.config)

                if models:
                    self.config.update_stored_models("llamacpp", models)

                    # Auto-select first real model if current is just "model"
                    current = self.config.get("llamacpp_model", "model")
                    if current == "model" and models:
                        self.config.set("llamacpp_model", models[0])
                        self.config.save_config()

                    return jsonify({"success": True, "models": models})

                # Try to get model info from /slots if /v1/models didn't work
                base = self.config.get("llamacpp_url", "http://127.0.0.1:8080").rstrip("/")
                try:
                    r = requests.get(f"{base}/slots", timeout=5)
                    if r.status_code == 200:
                        slots = r.json()
                        if not isinstance(slots, list):
                            slots = [slots] if slots else []
                        slot_models = []
                        for slot in slots:
                            model_path = slot.get("model", "") or slot.get("model_path", "")
                            if model_path:
                                # Extract just the filename
                                model_name = model_path.split("/")[-1].split("\\")[-1]
                                if model_name and model_name not in slot_models:
                                    slot_models.append(model_name)
                        if slot_models:
                            self.config.update_stored_models("llamacpp", slot_models)
                            # Auto-select if current is just "model"
                            current = self.config.get("llamacpp_model", "model")
                            if current == "model":
                                self.config.set("llamacpp_model", slot_models[0])
                                self.config.save_config()
                            return jsonify({"success": True, "models": slot_models})
                except:
                    pass

                # Return stored models if we can't fetch new ones
                stored = self.config.get("stored_llamacpp_models", [])
                return jsonify({"success": True, "models": stored, "note": "Using cached models"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e), "models": []}), 500

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

        # --- Ollama: start server (best-effort) ---
        @app.route('/api/ollama/start', methods=['POST'])
        def ollama_start():
            ok = start_ollama_server()
            return jsonify({"success": ok})

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

        # For Ollama: use stored list only (no network call on startup)
        if provider == 'ollama':
            stored = self.config.get("stored_ollama_models", [])
            # Fallback to defaults known by ConfigManager if stored is empty
            return stored or self.config.get_models_for_provider(provider)

        # Everyone else: use ConfigManager's provider lists (already cached/stored)
        return self.config.get_models_for_provider(provider)

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