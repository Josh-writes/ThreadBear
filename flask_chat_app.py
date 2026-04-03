"""
Flask-based AI Chat Application (stable routes, binary-safe uploads)
"""
from __future__ import annotations
import os

# Load .env before anything reads os.getenv
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import json
import shutil
import sqlite3
import subprocess
import threading
import time
import re
import uuid
from datetime import datetime
from typing import Dict, List
import requests
from api_clients import estimate_tokens, get_llamacpp_context_size
from threadbear_services import (
    BUILTIN_PROVIDERS,
    KNOWN_OPENAI_COMPAT_PROVIDERS,
    inject_endpoint_config,
    truncate_tool_result,
)

# No-proxy session for llama.cpp LAN/local calls
_local_session = requests.Session()
_local_session.trust_env = False
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
from message_compaction import MessageCompactor
from branch_db import BranchDatabase
from branch_manager import BranchManager
from folder_manager import FolderManager
from tools import tool_registry, ToolSafetyManager
from cost_tracker import calculate_cost as calc_api_cost
from api_clients import (
    call_groq_stream, call_google_stream,
    call_mistral_stream, call_openrouter_stream, call_llamacpp_stream,
    call_openai_compat_stream, fetch_openai_compat_catalog,
)
from context_documents import (
    list_documents, save_document, delete_document, get_document,
)
from readers import reader_registry
from error_classifier import LLMApiError, classify_error, friendly_message, ErrorClass
from content_security import wrap_external_content, truncate_head_tail
from tools.script_sandbox import ScriptScanner, SandboxedRunner, default_permissions, permissive_defaults

# =============================================================================
# Request-context safety: per-request state with thread-safe access
# =============================================================================
# These module-level variables replace instance-level state for concurrency safety.
# Critical for Phase 4 where multiple agent branches run concurrent LLM calls.

_request_contexts: Dict[int, Dict[str, Any]] = {}
_request_lock = threading.Lock()


def _set_request_context(message_id: int, **kwargs):
    """Store per-request state safely."""
    with _request_lock:
        if message_id not in _request_contexts:
            _request_contexts[message_id] = {}
        _request_contexts[message_id].update(kwargs)


def _get_request_context(message_id: int) -> Dict[str, Any]:
    """Retrieve per-request state."""
    with _request_lock:
        return _request_contexts.get(message_id, {})


def _clear_request_context(message_id: int):
    """Clean up after streaming completes."""
    with _request_lock:
        _request_contexts.pop(message_id, None)


def _cancel_generation(message_id: int):
    """Set cancel flag for a specific request."""
    with _request_lock:
        if message_id in _request_contexts:
            _request_contexts[message_id]['cancel_generation'] = True


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
        self.config.migrate_llamacpp_saved_urls()
        # No longer auto-saves API keys to config.json - they're read from environment variables
        self.chat_manager = ChatManager()
        self.compactor = MessageCompactor(config_manager=self.config)
        self.branch_db = BranchDatabase()
        self.branch_manager = BranchManager(self.branch_db)
        self.folder_manager = FolderManager(chats_directory="chats")
        self.chat_manager.branch_db = self.branch_db  # share with chat_manager
        # Migrate existing JSON chats into the branch database
        self.branch_db.migrate_from_json(self.chat_manager.chats_directory)

        self.temporary_mode = False
        self.incognito_mode = False
        self.builtin_providers = list(BUILTIN_PROVIDERS)
        self.known_providers = dict(KNOWN_OPENAI_COMPAT_PROVIDERS)

        self.pending_messages: Dict[int, Dict[str, str]] = {}

        self.setup_routes()
        self.server = None

    @property
    def available_providers(self):
        return self.builtin_providers + list(self.config.get("custom_endpoints", {}).keys())

    def _get_stream_func(self, provider):
        """Return the streaming function for a provider (builtin or custom endpoint)."""
        builtin = {
            "groq": call_groq_stream,
            "google": call_google_stream,
            "mistral": call_mistral_stream,
            "openrouter": call_openrouter_stream,
            "llamacpp": call_llamacpp_stream,
        }
        if provider in builtin:
            return builtin[provider]
        if provider in self.config.get("custom_endpoints", {}):
            return call_openai_compat_stream
        return None

    def _inject_endpoint_config(self, provider, merged_cfg):
        """Inject base URL + API key for known/custom OpenAI-compatible endpoints."""
        inject_endpoint_config(provider, merged_cfg, self.config)

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

            # For llamacpp, query the server for the actual loaded model
            if current_provider == "llamacpp":
                models, current_model = self._get_llamacpp_live_model()
            else:
                models = self.get_provider_models(current_provider)
                current_model = self.config.get(f"{current_provider}_model", "")
                if not current_model or current_model not in models:
                    current_model = models[0] if models else ""
                    if current_model:
                        self.config.set(f"{current_provider}_model", current_model)
                        self.config.save_config()

            # Build display name map for custom endpoints
            endpoint_names = {}
            for eid, ecfg in self.config.get("custom_endpoints", {}).items():
                endpoint_names[eid] = ecfg.get("name", eid)

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
                "endpoint_names": endpoint_names,
            })

        @app.route('/api/models/<provider>')
        def get_models(provider: str):
            if provider == "llamacpp":
                models, current_model = self._get_llamacpp_live_model()
            else:
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
            BROWSEABLE = ("openrouter", "groq", "google", "mistral")
            custom_endpoints = self.config.get("custom_endpoints", {})
            if provider not in BROWSEABLE and provider not in custom_endpoints:
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
            BROWSEABLE = ("openrouter", "groq", "google", "mistral")
            custom_endpoints = self.config.get("custom_endpoints", {})
            if provider not in BROWSEABLE and provider not in custom_endpoints:
                return jsonify({"success": False, "error": f"Provider '{provider}' not browseable"}), 400
            return jsonify(self.config.get(f"{provider}_catalog", []))

        @app.route('/api/browse/<provider>/refresh', methods=['POST'])
        def refresh_browse_catalog(provider):
            BROWSEABLE = ("openrouter", "groq", "google", "mistral")
            custom_endpoints = self.config.get("custom_endpoints", {})
            if provider not in BROWSEABLE and provider not in custom_endpoints:
                return jsonify({"success": False, "error": f"Provider '{provider}' not browseable"}), 400

            if provider in custom_endpoints:
                # Custom OpenAI-compatible endpoint
                ep = custom_endpoints[provider]
                api_key = self.config.get_api_key(provider)
                catalog = fetch_openai_compat_catalog(ep["base_url"], api_key)
            elif provider == "openrouter":
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

        # ---- Custom Endpoints CRUD ----
        @app.route('/api/endpoints', methods=['GET'])
        def list_endpoints():
            endpoints = self.config.get_custom_endpoints()
            return jsonify({"endpoints": endpoints, "known_providers": self.known_providers})

        @app.route('/api/endpoints', methods=['POST'])
        def create_endpoint():
            data = request.get_json() or {}
            name = (data.get("name") or "").strip()
            if not name:
                return jsonify({"success": False, "error": "Provider name is required"}), 400

            # Generate a safe ID from the name
            eid = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
            if not eid:
                return jsonify({"success": False, "error": "Invalid name"}), 400

            # Look up known provider defaults by the slug
            known = self.known_providers.get(eid, {})

            base_url = (data.get("base_url") or "").strip().rstrip("/")
            if not base_url:
                base_url = known.get("base_url", "")
            if not base_url:
                return jsonify({"success": False, "error": "Base URL is required for unknown providers"}), 400

            # Auto-generate env var name from provider name if not supplied
            api_key_env = (data.get("api_key_env") or "").strip()
            if not api_key_env:
                api_key_env = re.sub(r'[^A-Z0-9]+', '_', name.upper()).strip('_') + "_API_KEY"

            context_window = int(data.get("context_window") or known.get("context_window", 32768))

            # Prevent collision with builtins
            if eid in self.builtin_providers:
                eid = eid + "_custom"
            # Prevent collision with existing custom endpoints
            existing = self.config.get_custom_endpoints()
            if eid in existing:
                return jsonify({"success": False, "error": f"Endpoint '{eid}' already exists. Use the edit button to update it."}), 409

            ep_cfg = {
                "name": name,
                "base_url": base_url,
                "api_key_env": api_key_env,
                "api_key": (data.get("api_key") or "").strip(),
                "default_model": (data.get("default_model") or "").strip(),
                "context_window": context_window,
            }
            self.config.save_endpoint(eid, ep_cfg)
            return jsonify({"success": True, "id": eid, "endpoint": ep_cfg})

        @app.route('/api/endpoints/<eid>', methods=['PUT'])
        def update_endpoint(eid):
            data = request.get_json() or {}
            existing = self.config.get_endpoint_config(eid)
            if not existing:
                return jsonify({"success": False, "error": "Endpoint not found"}), 404
            # Update fields if provided
            if "name" in data: existing["name"] = data["name"].strip()
            if "base_url" in data: existing["base_url"] = data["base_url"].strip().rstrip("/")
            if "api_key_env" in data: existing["api_key_env"] = data["api_key_env"].strip()
            if "api_key" in data: existing["api_key"] = data["api_key"].strip()
            if "default_model" in data: existing["default_model"] = data["default_model"].strip()
            if "context_window" in data: existing["context_window"] = int(data["context_window"])
            self.config.save_endpoint(eid, existing)
            return jsonify({"success": True, "endpoint": existing})

        @app.route('/api/endpoints/<eid>', methods=['DELETE'])
        def delete_endpoint(eid):
            if self.config.delete_endpoint(eid):
                return jsonify({"success": True})
            return jsonify({"success": False, "error": "Endpoint not found"}), 404

        @app.route('/api/endpoints/<eid>/test', methods=['POST'])
        def test_endpoint(eid):
            ep = self.config.get_endpoint_config(eid)
            if not ep:
                return jsonify({"success": False, "error": "Endpoint not found"}), 404
            api_key = self.config.get_api_key(eid)
            try:
                catalog = fetch_openai_compat_catalog(ep["base_url"], api_key)
                if catalog:
                    return jsonify({"success": True, "model_count": len(catalog),
                                    "models": [m["id"] for m in catalog[:5]]})
                return jsonify({"success": False, "error": "No models returned (check URL and API key)"}), 502
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 502

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

                data = request.get_json() or {}
                folder_id = data.get('folder_id')

                filename = self.chat_manager.create_new_chat()

                # Assign chat to folder if specified
                if folder_id:
                    self.folder_manager.assign_chat_to_folder(filename, folder_id)

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
            old_provider = self.config.get("provider", "groq")
            provider = data.get('provider', old_provider)

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

            provider = data.get('provider') or self.config.get("provider")
            model = data.get('model') or self.config.get(f"{provider}_model")

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
            last_renamed_chat = None
            if not self.incognito_mode:
                # Track the current chat file before adding the message
                old_filename = self.chat_manager.current_chat_file
                self.chat_manager.add_message("user", message)
                # Check if the chat was auto-renamed (first message)
                new_filename = self.chat_manager.current_chat_file
                if old_filename != new_filename:
                    last_renamed_chat = new_filename

            # Store selected context and rename info in the per-request dict
            has_selected_context = ('selected_context' in data)
            _set_request_context(mid,
                has_selected_context=has_selected_context,
                selected_context=data.get('selected_context') if has_selected_context else None,
                selected_summaries=data.get('selected_summaries', []),
                last_renamed_chat=last_renamed_chat,
                provider=provider,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                system_prompt=system_prompt,
                cancel_generation=False
            )

            print("SEND PAYLOAD:", {"provider": provider, "model": model, "mid": mid})
            response_data = {"success": True, "message_id": mid, "current_chat_file": self.chat_manager.current_chat_file}
            # Include the renamed chat file if applicable
            if last_renamed_chat:
                response_data["filename"] = last_renamed_chat
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

            # System prompt - use config method that falls back to templates
            system_prompt = self.config.get_system_prompt(provider, model)
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
                    _ctx_cfg = dict(self.config.config)
                    _ctx_cfg["llamacpp_url"] = self._get_llamacpp_url()
                    auto = get_llamacpp_context_size(_ctx_cfg)
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
                    # Get per-request data (contains provider settings + context selection)
                    snap = _get_request_context(message_id)

                    # Build API messages
                    if self.incognito_mode:
                        msgs = self.chat_manager.get_messages()
                        if msgs and msgs[-1]["role"] == "user":
                            api_messages = [{"role": "user", "content": msgs[-1]["content"]}]
                        else:
                            yield f"data: {json.dumps({'type':'error','content':'No message found'})}\n\n"
                            yield f"data: {json.dumps({'type':'complete'})}\n\n"
                            return
                    else:
                        # Read context selection from per-request snap (not self)
                        has_selected = snap.get('has_selected_context', False)
                        selected_msgs = snap.get('selected_context') or []
                        selected_sums = snap.get('selected_summaries') or []

                        if has_selected:
                            # Client explicitly provided selection
                            if len(selected_msgs) > 0 or len(selected_sums) > 0:
                                api_messages = self.chat_manager.get_selected_context(
                                    selected_msgs,
                                    selected_sums
                                )
                            else:
                                # Explicit NONE => only the latest user prompt (no history)
                                msgs = self.chat_manager.get_messages()
                                last_user = next((m for m in reversed(msgs) if m.get("role") == "user"), None)
                                if not last_user:
                                    yield f"data: {json.dumps({'type':'error','content':'No user message found'})}\n\n"
                                    yield f"data: {json.dumps({'type':'complete'})}\n\n"
                                    return
                                api_messages = [{"role": "user", "content": last_user.get("content", "")}]
                        else:
                            # No explicit selection provided: use normal conversation context
                            api_messages = self.chat_manager.get_conversation_context()

                        # ALWAYS include documents, regardless of selection mode
                        docs = context_documents.build_context_injections()
                        api_messages.extend(docs)

                    # Inject folder context if chat is in a folder
                    current_file = self.chat_manager.current_chat_file
                    if not self.incognito_mode and current_file:
                        folder_id = self.folder_manager.get_chat_folder(current_file)
                        if folder_id and not self.folder_manager.is_prompt_branch(current_file):
                            folder_ctx = self._build_folder_context(folder_id)
                            api_messages = folder_ctx + api_messages

                    # Extract provider/model from snap (use config as fallback if snap is empty)
                    if not snap:
                        provider = self.config.get("provider")
                        model = self.config.get(f"{provider}_model")
                        ms = self.config.get_model_settings(provider, model)
                        temperature = self.config.get(f"{provider}_temperature", 0.7)
                        temperature = ms.get("temperature", temperature)
                        max_tokens = self.config.get(f"{provider}_max_tokens", 4096)
                        max_tokens = ms.get("max_tokens", max_tokens)
                        # Use config method that falls back to templates
                        system_prompt = self.config.get_system_prompt(provider, model)
                    else:
                        provider = snap.get('provider', self.config.get("provider"))
                        model = snap.get('model', self.config.get(f"{provider}_model"))
                        temperature = snap.get('temperature', self.config.get(f"{provider}_temperature", 0.7))
                        max_tokens = snap.get('max_tokens', self.config.get(f"{provider}_max_tokens", 4096))
                        # For branch snapshots, use stored system_prompt or template
                        system_prompt = snap.get('system_prompt') or self.config.get_system_prompt(provider, model)

                    # === MESSAGE COMPACTION (before LLM call) ===
                    # Compact api_messages if approaching context window limit
                    try:
                        context_window = self.config.get_context_window(provider, model)
                        if self.compactor.should_compact(api_messages, provider, model):
                            compacted, summary = self.compactor.compact_messages(
                                api_messages, provider, model
                            )
                            api_messages = compacted
                            print(f"[Compaction] Applied before LLM call: {len(api_messages)} messages, {summary[:50] if summary else 'no summary'}...")
                    except Exception as compact_err:
                        print(f"Pre-LLM compaction failed (non-blocking): {compact_err}")

                    stream_func = self._get_stream_func(provider)
                    if not stream_func:
                        yield f"data: {json.dumps({'type':'error','content':f'Unknown provider: {provider}'})}\n\n"
                        return

                    # Send model info first (for llamacpp, query the actual loaded model)
                    display_model = model
                    if provider == "llamacpp":
                        live_models, live_current = self._get_llamacpp_live_model()
                        if live_current:
                            display_model = live_current
                    yield f"data: {json.dumps({'type':'model','content':display_model})}\n\n"

                    # Get per-request cancel flag from snap
                    snap['cancel_generation'] = False

                    # Build merged config to pass to API clients
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
                    # Optional per-model sampling params
                    ms = self.config.get_model_settings(provider, model)
                    if "top_p" in ms: merged_cfg["top_p"] = ms["top_p"]
                    if "top_k" in ms: merged_cfg["top_k"] = ms["top_k"]

                    # === TOOL EXECUTION LOOP (Phase 3) ===
                    tool_config = self.config.get_tool_config(provider)
                    tools_enabled = tool_config.get('enabled', False)
                    tool_schemas = tool_registry.get_schemas_for_provider() if tools_enabled else None

                    # Inject tool-awareness into the system prompt (via config,
                    # NOT api_messages, to avoid duplicate system messages that
                    # break strict chat templates like llama.cpp's Jinja)
                    if tools_enabled and tool_schemas:
                        tool_names = [t['function']['name'] for t in tool_schemas]
                        tool_os = self.config.get('tool_os', 'windows')
                        os_hints = {
                            'windows': "The user is on Windows. Use Windows commands (PowerShell/cmd), Windows file paths (backslashes), and Windows-compatible tools.",
                            'linux': "The user is on Linux. Use Bash/shell commands, Unix file paths (forward slashes), and Linux-compatible tools.",
                            'macos': "The user is on macOS. Use Bash/zsh commands, Unix file paths (forward slashes), and macOS-compatible tools (e.g. brew, open).",
                        }
                        tool_hint = (
                            "\n\nYou have access to the following tools: "
                            + ", ".join(tool_names) + ". "
                            + os_hints.get(tool_os, os_hints['windows']) + " "
                            "When the user asks you to write files, run commands, list directories, "
                            "or perform actions on their system, USE the tools directly instead of "
                            "just showing code. Execute the actions using your tools. "
                            "After running tools, present the key results clearly in your response. "
                            "For command output, include the relevant output directly in your reply "
                            "rather than just saying 'done'."
                        )

                        # Deep search instruction (only if web_search is available)
                        if 'web_search' in tool_names:
                            tool_hint += (
                                "\n\nWEB SEARCH STRATEGY: For complex or multi-faceted questions, "
                                "break the question into 2-4 focused sub-queries and search each separately. "
                                "After each search, evaluate whether you have enough information to answer comprehensively. "
                                "If not, refine your query or search for missing aspects. "
                                "Synthesize information from all searches into a thorough answer."
                                "\n\nSOURCE CITATION: ALWAYS include sources in your final response. "
                                "Format each source as a markdown link: [Page Title](https://url). "
                                "Place a 'Sources' section at the end of your response listing all URLs you retrieved information from. "
                                "When referencing specific facts, use inline links like [source](url) in the text."
                            )

                        # Project paths for tool awareness
                        project_root = os.path.dirname(os.path.abspath(__file__))
                        tool_hint += (
                            f"\n\nPROJECT PATHS:"
                            f"\n- Project root: {project_root}"
                            f"\n- Toolbox: {os.path.join(project_root, 'toolbox')}"
                            f"\nWhen the user asks you to write a script or tool, save it to the "
                            f"toolbox directory listed above. Always use absolute paths."
                        )

                        system_prompt = (system_prompt or "") + tool_hint
                        # Update merged_cfg so API clients use the combined prompt
                        merged_cfg["system_prompt"] = system_prompt
                        merged_cfg[f"{provider}_system_prompt"] = system_prompt
                    safety_mgr = ToolSafetyManager({
                        'blocked_commands': tool_config.get('blocked_commands', []),
                        'tool_workspace': tool_config.get('workspace'),
                    }) if tools_enabled else None
                    max_iterations = tool_config.get('max_iterations', 5)

                    full_response = ""
                    stream_usage = None  # Capture real token usage from provider
                    tool_events_log = []  # Persist tool events for reload
                    working_texts = []   # Capture intermediate LLM text (working text)
                    max_overflow_retries = 2
                    for iteration in range(max_iterations):
                        # Rate-limit protection: pause between tool loop iterations
                        if iteration > 0:
                            time.sleep(2)
                            # Slim messages: keep system + last user msg + tool pairs only
                            # This dramatically reduces token count on iterations 1+
                            # Also drop empty/failed tool results to save tokens
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
                            # Track which tool_call_ids had empty results so we can drop their pairs
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
                                    # Filter out tool_calls whose results were empty
                                    if empty_tool_ids:
                                        kept = [tc for tc in m['tool_calls'] if tc.get('id', '') not in empty_tool_ids]
                                        if kept:
                                            trimmed = dict(m)
                                            trimmed['tool_calls'] = kept
                                            slim_messages.append(trimmed)
                                        # If all tool_calls in this msg were empty, drop the whole msg
                                    else:
                                        slim_messages.append(m)
                            api_messages = slim_messages

                        tool_calls_this_round = []
                        content_buffer = ""

                        # Call LLM with overflow retry
                        for overflow_attempt in range(max_overflow_retries + 1):
                            try:
                                tool_calls_this_round = []
                                content_buffer = ""
                                for chunk in stream_func(api_messages, merged_cfg, tools=tool_schemas):
                                    if snap.get('cancel_generation', False):
                                        yield f"data: {json.dumps({'type':'error','content':'Generation cancelled'})}\n\n"
                                        return

                                    if isinstance(chunk, dict) and chunk.get('type') == 'tool_calls':
                                        tool_calls_this_round = chunk.get('tool_calls', [])
                                    elif isinstance(chunk, dict) and chunk.get('type') == 'usage':
                                        stream_usage = chunk
                                    elif isinstance(chunk, str):
                                        content_buffer += chunk
                                        yield f"data: {json.dumps({'type':'content','content':chunk})}\n\n"
                                        time.sleep(0.005)
                                break  # success — exit retry loop
                            except LLMApiError as overflow_err:
                                cls = classify_error(overflow_err.status_code, overflow_err.response_text)
                                if cls == ErrorClass.CONTEXT_OVERFLOW and overflow_attempt < max_overflow_retries:
                                    yield f"data: {json.dumps({'type':'status','content':'Context too large, compacting...'})}\n\n"
                                    compacted, _ = self.compactor.compact_messages(
                                        api_messages, provider, model, force=True
                                    )
                                    api_messages = compacted
                                    content_buffer = ""
                                    tool_calls_this_round = []
                                    continue
                                raise  # re-raise for outer handler

                        if not tool_calls_this_round:
                            full_response = content_buffer
                            break  # No tools called — LLM gave final response

                        # Capture intermediate text as working text
                        if content_buffer and content_buffer.strip():
                            working_texts.append(content_buffer.strip())

                        # Add assistant message with tool calls to history
                        assistant_msg = {'role': 'assistant', 'content': content_buffer or None}
                        if tool_calls_this_round:
                            assistant_msg['tool_calls'] = tool_calls_this_round
                        api_messages.append(assistant_msg)

                        # Execute each tool call
                        for tc in tool_calls_this_round:
                            name = tc.get('function', {}).get('name', '')
                            try:
                                args = json.loads(tc.get('function', {}).get('arguments', '{}'))
                            except json.JSONDecodeError:
                                args = {}

                            # Yield tool start event
                            yield f"data: {json.dumps({'type':'tool_start', 'name': name, 'args': args})}\n\n"
                            tool_events_log.append({'name': name, 'args': args, 'status': 'running', 'result': None})

                            # Execute with safety check
                            result = tool_registry.execute_tool(name, args, safety_mgr)

                            # Yield tool end event
                            yield f"data: {json.dumps({'type':'tool_end', 'name': name, 'result': result})}\n\n"
                            # Update the matching log entry
                            for te in reversed(tool_events_log):
                                if te['name'] == name and te['status'] == 'running':
                                    te['status'] = 'success' if result.get('success', True) else 'error'
                                    te['result'] = result
                                    break

                            # Add tool result to messages for next LLM call
                            # Compact empty/failed results to a minimal stub
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
                                # Budget: 40% of context window for tool results,
                                # ~4 chars per token, split across tool calls this round
                                try:
                                    ctx_window = self.config.get_context_window(provider, model)
                                except Exception:
                                    ctx_window = 8192
                                budget_chars = int(ctx_window * 0.4 * 4) // max(len(tool_calls_this_round), 1)
                                budget_chars = max(budget_chars, 2000)  # floor
                                llm_result = truncate_tool_result(result, max_chars=budget_chars)
                                api_messages.append({
                                    'role': 'tool',
                                    'tool_call_id': tc.get('id', ''),
                                    'content': json.dumps(llm_result)
                                })

                        # Loop continues — LLM gets tool results and generates next response
                    else:
                        # Loop exhausted max_iterations while model was still calling tools.
                        # Force one final LLM call WITHOUT tools so it synthesizes a response.
                        try:
                            yield f"data: {json.dumps({'type':'status','content':'Synthesizing final response...'})}\n\n"
                            for chunk in stream_func(api_messages, merged_cfg, tools=None):
                                if isinstance(chunk, dict) and chunk.get('type') == 'usage':
                                    stream_usage = chunk
                                elif isinstance(chunk, str):
                                    full_response += chunk
                                    yield f"data: {json.dumps({'type':'content','content':chunk})}\n\n"
                                    time.sleep(0.005)
                        except Exception as synth_err:
                            print(f"Synthesis call failed: {synth_err}")
                            if not full_response:
                                full_response = "\n\n".join(working_texts) if working_texts else "(Tool results above — model did not generate a summary)"

                    if not self.temporary_mode and not self.incognito_mode:
                        self.chat_manager.add_message("assistant", full_response, model)
                        msgs = self.chat_manager.current_chat.get("chat_history", [])
                        if msgs:
                            # Persist tool events and working text for reload
                            if tool_events_log:
                                msgs[-1]["tool_events"] = tool_events_log
                            if working_texts:
                                msgs[-1]["workingText"] = "\n\n".join(working_texts)
                            # Store usage data on the message if available
                            if stream_usage:
                                msgs[-1]["usage"] = {
                                    "input_tokens": stream_usage.get("input_tokens", 0),
                                    "output_tokens": stream_usage.get("output_tokens", 0),
                                }
                                # Calculate and store cost
                                msg_cost = calc_api_cost(
                                    provider, model,
                                    stream_usage.get("input_tokens", 0),
                                    stream_usage.get("output_tokens", 0)
                                )
                                msgs[-1]["cost"] = msg_cost
                                msgs[-1]["provider"] = provider
                            self.chat_manager.save_current_chat()

                    # --- Auto-title generation after first exchange ---
                    # Works for: new chats, branched chats, folder chats (all treated the same)
                    try:
                        chat_hist = self.chat_manager.current_chat.get("chat_history", [])
                        current_title = self.chat_manager.current_chat.get("title", "")
                        
                        # Generate title if:
                        # 1. Not temporary/incognito mode
                        # 2. Title is default ("New Chat") OR empty (branched chats)
                        # 3. Exactly 2 messages (first user-assistant exchange)
                        # 4. Not a prompt branch chat (those have special handling)
                        is_prompt_branch = self.folder_manager.is_prompt_branch(
                            self.chat_manager.current_chat_file
                        ) if self.chat_manager.current_chat_file else False
                        
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

                            if title_stream:
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
                                    # Skip dict chunks (usage data) - only accumulate string content
                                    if isinstance(chunk, dict):
                                        continue
                                    generated += chunk

                                generated = generated.strip().strip('"').strip("'").strip()
                                if generated:
                                    generated = generated[:60]
                                    if self.chat_manager.update_title(generated):
                                        yield f"data: {json.dumps({'type':'title','title':generated,'filename':self.chat_manager.current_chat_file})}\n\n"
                    except Exception as title_err:
                        print(f"Auto-title generation failed: {title_err}")

                    complete_event = {'type': 'complete'}
                    if stream_usage:
                        complete_event['usage'] = {
                            'input_tokens': stream_usage.get('input_tokens', 0),
                            'output_tokens': stream_usage.get('output_tokens', 0),
                        }
                        # Include cost in complete event
                        msg_cost = calc_api_cost(
                            provider, model,
                            stream_usage.get('input_tokens', 0),
                            stream_usage.get('output_tokens', 0)
                        )
                        complete_event['cost'] = msg_cost
                    yield f"data: {json.dumps(complete_event)}\n\n"
                except LLMApiError as api_err:
                    cls = classify_error(api_err.status_code, api_err.response_text)
                    msg = friendly_message(cls, api_err.provider, api_err.status_code, api_err.response_text)
                    yield f"data: {json.dumps({'type':'error','content':msg,'error_class':cls.value})}\n\n"
                    yield f"data: {json.dumps({'type':'complete'})}\n\n"
                except Exception as e:
                    err = f"Error: {e}"
                    yield f"data: {json.dumps({'type':'error','content':err})}\n\n"
                    yield f"data: {json.dumps({'type':'complete'})}\n\n"
                finally:
                    # Clean up per-request context after streaming completes
                    _clear_request_context(message_id)
            return Response(generate(), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'})

        # ---- Tool System API (Phase 3) ----

        @app.route('/api/tools', methods=['GET'])
        def list_tools():
            """List all registered tools with schemas and metadata."""
            tools = tool_registry.list_tools()
            schemas = tool_registry.get_schemas_for_provider()
            return jsonify({
                "success": True,
                "tools": tools,
                "schemas": schemas
            })

        @app.route('/api/config/tools', methods=['GET'])
        def get_tools_config():
            """Get tool enablement status for all providers."""
            providers = self.available_providers
            config = {}
            for p in providers:
                config[p] = self.config.get(f'{p}_tools_enabled', False)
            config['max_iterations'] = self.config.get('max_tool_iterations', 5)
            config['timeout'] = self.config.get('tool_execution_timeout', 30)
            config['tool_os'] = self.config.get('tool_os', 'windows')
            return jsonify({"success": True, "config": config})

        @app.route('/api/config/tools', methods=['POST'])
        def set_tools_config():
            """Enable/disable tools for a provider."""
            data = request.get_json() or {}
            provider = data.get('provider')
            enabled = data.get('enabled', False)

            if not provider:
                return jsonify({"success": False, "error": "provider required"}), 400

            if provider not in self.available_providers:
                return jsonify({"success": False, "error": f"Unknown provider: {provider}"}), 400

            self.config.set(f'{provider}_tools_enabled', enabled)
            self.config.save_config()

            return jsonify({"success": True})

        @app.route('/api/config/tools/os', methods=['POST'])
        def set_tool_os():
            """Set the operating system hint for tool commands."""
            data = request.get_json() or {}
            tool_os = data.get('tool_os', 'windows')
            if tool_os not in ('windows', 'linux', 'macos'):
                return jsonify({"success": False, "error": "Invalid OS"}), 400
            self.config.set('tool_os', tool_os)
            self.config.save_config()
            return jsonify({"success": True})

        # ---- Editor preference ----

        # Common install paths to check beyond PATH
        _EDITOR_SEARCH_PATHS = [
            os.path.join(os.environ.get("ProgramFiles", ""), "Notepad++", "notepad++.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Notepad++", "notepad++.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Microsoft VS Code", "Code.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Sublime Text", "sublime_text.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Sublime Text 3", "sublime_text.exe"),
        ]

        def _find_editor(cmd):
            """Find an editor: check PATH first, then common install locations."""
            found = shutil.which(cmd)
            if found:
                return found
            # Check common install paths
            for p in _EDITOR_SEARCH_PATHS:
                if os.path.basename(p).lower() == cmd.lower() and os.path.isfile(p):
                    return p
            return None

        KNOWN_EDITORS = [
            {"id": "code", "name": "VS Code", "cmd": "code"},
            {"id": "notepad++", "name": "Notepad++", "cmd": "notepad++.exe"},
            {"id": "sublime", "name": "Sublime Text", "cmd": "sublime_text.exe"},
            {"id": "notepad", "name": "Notepad", "cmd": "notepad.exe"},
        ]

        @app.route('/api/config/editor', methods=['GET'])
        def get_editor_config():
            """Return available editors and the current preference."""
            available = []
            for ed in KNOWN_EDITORS:
                path = _find_editor(ed["cmd"])
                available.append({**ed, "available": path is not None, "path": path or ""})
            current = self.config.get("preferred_editor", "")
            # If no preference set, default to first available
            if not current:
                first = next((e["id"] for e in available if e["available"]), "notepad")
                current = first
            return jsonify({"success": True, "editors": available, "preferred": current})

        @app.route('/api/config/editor', methods=['POST'])
        def set_editor_config():
            """Set the preferred editor (id or custom command)."""
            data = request.get_json() or {}
            editor = data.get("editor", "").strip()
            if not editor:
                return jsonify({"success": False, "error": "No editor specified"}), 400
            self.config.set("preferred_editor", editor)
            self.config.save_config()
            return jsonify({"success": True, "preferred": editor})

        # ---- Toolbox file management ----

        _APP_ROOT = os.path.dirname(os.path.abspath(__file__))
        TOOLBOX_DIR = os.path.join(_APP_ROOT, 'toolbox')
        DEFAULT_TOOLBOX_DIR = os.path.join(_APP_ROOT, 'default_toolbox')

        def _resolve_toolbox_file(filename):
            """Resolve a toolbox filename to its absolute path.

            Custom (toolbox/) takes priority over default (default_toolbox/).
            Returns (abs_path, is_default) or (None, False) if not found.
            """
            custom = os.path.join(TOOLBOX_DIR, filename)
            if os.path.isfile(custom):
                return custom, False
            default = os.path.join(DEFAULT_TOOLBOX_DIR, filename)
            if os.path.isfile(default):
                return default, True
            return None, False

        @app.route('/api/toolbox/files', methods=['GET'])
        def list_toolbox_files():
            """List files from both default_toolbox/ and toolbox/ directories."""
            os.makedirs(TOOLBOX_DIR, exist_ok=True)
            seen = {}
            # Custom scripts first (override defaults with same name)
            for name in sorted(os.listdir(TOOLBOX_DIR)):
                if name.startswith('.'):
                    continue
                fpath = os.path.join(TOOLBOX_DIR, name)
                if os.path.isfile(fpath):
                    stat = os.stat(fpath)
                    seen[name] = {
                        'name': name,
                        'size': stat.st_size,
                        'modified': stat.st_mtime,
                        'source': 'custom',
                    }
            # Default scripts (only if not overridden)
            if os.path.isdir(DEFAULT_TOOLBOX_DIR):
                for name in sorted(os.listdir(DEFAULT_TOOLBOX_DIR)):
                    if name.startswith('.') or name in seen:
                        continue
                    fpath = os.path.join(DEFAULT_TOOLBOX_DIR, name)
                    if os.path.isfile(fpath):
                        stat = os.stat(fpath)
                        seen[name] = {
                            'name': name,
                            'size': stat.st_size,
                            'modified': stat.st_mtime,
                            'source': 'default',
                        }
            files = sorted(seen.values(), key=lambda f: f['name'])
            return jsonify({"success": True, "files": files})

        @app.route('/api/toolbox/files/<path:filename>', methods=['GET'])
        def read_toolbox_file(filename):
            """Read contents of a toolbox file (custom or default)."""
            fpath, is_default = _resolve_toolbox_file(filename)
            if not fpath:
                return jsonify({"success": False, "error": "File not found"}), 404
            try:
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                return jsonify({"success": True, "content": content, "name": filename,
                                "source": "default" if is_default else "custom"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/toolbox/files/<path:filename>', methods=['DELETE'])
        def delete_toolbox_file(filename):
            """Delete a file from toolbox/ (cannot delete defaults)."""
            fpath, is_default = _resolve_toolbox_file(filename)
            if not fpath:
                return jsonify({"success": False, "error": "File not found"}), 404
            if is_default:
                return jsonify({"success": False, "error": "Cannot delete a default toolbox script"}), 400
            try:
                os.remove(fpath)
                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/toolbox/open/<path:filename>', methods=['POST'])
        def open_toolbox_file(filename):
            """Open a toolbox file in the user's preferred editor."""
            fpath, _ = _resolve_toolbox_file(filename)
            if not fpath:
                return jsonify({"success": False, "error": "File not found"}), 404
            pref = self.config.get("preferred_editor", "")
            # Resolve the command from known editors or use as custom command
            cmd = None
            for ed in KNOWN_EDITORS:
                if ed["id"] == pref:
                    resolved = _find_editor(ed["cmd"])
                    cmd = resolved or ed["cmd"]
                    break
            if not cmd:
                cmd = pref or "notepad.exe"
            try:
                subprocess.Popen([cmd, os.path.abspath(fpath)])
                return jsonify({"success": True})
            except FileNotFoundError:
                subprocess.Popen(['notepad.exe', os.path.abspath(fpath)])
                return jsonify({"success": True, "fallback": "notepad"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/toolbox/explore/<path:filename>', methods=['POST'])
        def explore_toolbox_file(filename):
            """Open File Explorer with the toolbox file selected."""
            fpath, _ = _resolve_toolbox_file(filename)
            if not fpath:
                return jsonify({"success": False, "error": "File not found"}), 404
            try:
                abs_path = os.path.abspath(fpath)
                subprocess.Popen(['explorer', '/select,', abs_path])
                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/toolbox/path/<path:filename>', methods=['GET'])
        def get_toolbox_path(filename):
            """Return the absolute path of a toolbox file."""
            fpath, _ = _resolve_toolbox_file(filename)
            if not fpath:
                return jsonify({"success": False, "error": "File not found"}), 404
            return jsonify({"success": True, "path": os.path.abspath(fpath)})

        # ---- Toolbelt (per-chat script runner) ----

        _script_scanner = ScriptScanner()
        _sandboxed_runner = SandboxedRunner()

        def _normalize_toolbelt(raw):
            """Convert list-format toolbelt to dict format."""
            if isinstance(raw, list):
                return {s: permissive_defaults() for s in raw if isinstance(s, str)}
            if isinstance(raw, dict):
                return raw
            return {}

        def _get_toolbelt_for_chat(chat_file):
            """Get toolbelt dict for a chat, handling current vs on-disk."""
            if self.chat_manager.current_chat_file == chat_file:
                raw = self.chat_manager.current_chat.get("toolbelt", {})
                return _normalize_toolbelt(raw)
            chat_path = os.path.join(self.chat_manager.chats_directory, chat_file)
            if not os.path.isfile(chat_path):
                return None
            try:
                with open(chat_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return _normalize_toolbelt(data.get("toolbelt", {}) if isinstance(data, dict) else {})
            except Exception:
                return {}

        @app.route('/api/toolbelt/scan', methods=['POST'])
        def scan_toolbelt_script():
            """Scan a toolbox script for capabilities."""
            body = request.get_json() or {}
            script = body.get("script", "").strip()
            if not script:
                return jsonify({"success": False, "error": "Need script name"}), 400
            script_path, _ = _resolve_toolbox_file(script)
            if not script_path:
                return jsonify({"success": False, "error": f"Script '{script}' not found"}), 404
            scan_result = _script_scanner.scan(script_path)
            return jsonify({"success": True, "scan_result": scan_result})

        @app.route('/api/toolbelt/permissions/<path:filename>', methods=['POST'])
        def update_toolbelt_permissions(filename):
            """Update permissions for a script in a chat's toolbelt."""
            body = request.get_json() or {}
            script = body.get("script", "").strip()
            permissions = body.get("permissions", {})
            if not script:
                return jsonify({"success": False, "error": "Need script name"}), 400

            if self.chat_manager.current_chat_file == filename:
                tb = self.chat_manager.current_chat.setdefault("toolbelt", {})
                tb = _normalize_toolbelt(tb)
                self.chat_manager.current_chat["toolbelt"] = tb
                if script not in tb:
                    return jsonify({"success": False, "error": f"Script '{script}' not in toolbelt"}), 400
                tb[script].update(permissions)
                self.chat_manager.save_current_chat(force_save=True)
                return jsonify({"success": True, "toolbelt": tb})

            chat_path = os.path.join(self.chat_manager.chats_directory, filename)
            if not os.path.isfile(chat_path):
                return jsonify({"success": False, "error": "Chat not found"}), 404
            try:
                with open(chat_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return jsonify({"success": False, "error": "Invalid chat format"}), 400
                tb = _normalize_toolbelt(data.get("toolbelt", {}))
                data["toolbelt"] = tb
                if script not in tb:
                    return jsonify({"success": False, "error": f"Script '{script}' not in toolbelt"}), 400
                tb[script].update(permissions)
                with open(chat_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return jsonify({"success": True, "toolbelt": tb})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/toolbelt/run', methods=['POST'])
        def run_toolbelt_script():
            """Execute a toolbelt script in a sandboxed subprocess."""
            body = request.get_json() or {}
            script = body.get("script", "").strip()
            chat_file = body.get("chat_file", "").strip()
            if not script or not chat_file:
                return jsonify({"success": False, "error": "Need script and chat_file"}), 400

            script_path, _ = _resolve_toolbox_file(script)
            if not script_path:
                return jsonify({"success": False, "error": f"Script '{script}' not found in toolbox"}), 404

            chat_path = os.path.join(self.chat_manager.chats_directory, chat_file)
            if not os.path.isfile(chat_path):
                return jsonify({"success": False, "error": f"Chat file '{chat_file}' not found"}), 404

            # Get toolbelt and verify script is assigned
            tb = _get_toolbelt_for_chat(chat_file)
            if tb is None:
                return jsonify({"success": False, "error": "Chat not found"}), 404
            if script not in tb:
                return jsonify({"success": False, "error": f"Script '{script}' is not in this chat's toolbelt"}), 400

            # Run with sandbox
            permissions = tb[script]
            project_root = os.path.dirname(os.path.abspath(__file__))
            print(f"[toolbelt] Running '{script}' with permissions: env={permissions.get('allow_env', [])}, timeout={permissions.get('timeout', 60)}")
            result = _sandboxed_runner.run(script_path, chat_path, permissions, project_root)
            print(f"[toolbelt] Result: success={result['success']}, rc={result['returncode']}, stdout={len(result.get('output',''))} chars, stderr={len(result.get('error',''))} chars")
            if result.get('error'):
                print(f"[toolbelt] stderr: {result['error'][:500]}")
            status_code = 200 if result["success"] else (504 if result["returncode"] == -1 else 200)
            return jsonify(result), status_code

        @app.route('/api/toolbelt/<path:filename>', methods=['GET'])
        def get_toolbelt(filename):
            """Get the toolbelt (assigned scripts) for a chat."""
            tb = _get_toolbelt_for_chat(filename)
            if tb is None:
                return jsonify({"success": False, "error": "Chat not found"}), 404
            return jsonify({"success": True, "toolbelt": tb})

        @app.route('/api/toolbelt/<path:filename>', methods=['POST'])
        def update_toolbelt(filename):
            """Add or remove a script from a chat's toolbelt."""
            body = request.get_json() or {}
            script = body.get("script", "").strip()
            action = body.get("action", "")
            if not script or action not in ("add", "remove"):
                return jsonify({"success": False, "error": "Need script and action (add/remove)"}), 400

            if action == "add":
                script_path, _ = _resolve_toolbox_file(script)
                if not script_path:
                    return jsonify({"success": False, "error": f"Script '{script}' not found in toolbox"}), 404
                # Auto-scan on add
                scan_result = _script_scanner.scan(script_path)
                perms = default_permissions(scan_result)

            if self.chat_manager.current_chat_file == filename:
                tb = self.chat_manager.current_chat.setdefault("toolbelt", {})
                tb = _normalize_toolbelt(tb)
                self.chat_manager.current_chat["toolbelt"] = tb
                if action == "add":
                    tb[script] = perms
                elif action == "remove" and script in tb:
                    del tb[script]
                self.chat_manager.save_current_chat(force_save=True)
                return jsonify({"success": True, "toolbelt": tb})

            chat_path = os.path.join(self.chat_manager.chats_directory, filename)
            if not os.path.isfile(chat_path):
                return jsonify({"success": False, "error": "Chat not found"}), 404
            try:
                with open(chat_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return jsonify({"success": False, "error": "Invalid chat format"}), 400
                tb = _normalize_toolbelt(data.get("toolbelt", {}))
                data["toolbelt"] = tb
                if action == "add":
                    tb[script] = perms
                elif action == "remove" and script in tb:
                    del tb[script]
                with open(chat_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                return jsonify({"success": True, "toolbelt": tb})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- Folder management ----
        @app.route('/api/folders', methods=['GET'])
        def get_folders():
            """Get all folders in tree structure with mappings."""
            tree = self.folder_manager.get_folder_tree()
            mappings = self.folder_manager.get_all_mappings()
            return jsonify({
                "folders": tree,
                "chat_folder_map": mappings["chat_folder_map"],
                "file_folder_map": mappings["file_folder_map"],
            })

        @app.route('/api/folders', methods=['POST'])
        def create_folder():
            """Create a new folder."""
            data = request.get_json() or {}
            name = data.get('name', 'New Folder')
            parent_id = data.get('parent_id')
            try:
                folder = self.folder_manager.create_folder(name, parent_id)
                return jsonify({"success": True, "folder": folder})
            except ValueError as e:
                return jsonify({"success": False, "error": str(e)}), 400

        @app.route('/api/folders/<folder_id>', methods=['PUT'])
        def update_folder(folder_id: str):
            """Rename, move, or update workspace fields on a folder."""
            data = request.get_json() or {}
            try:
                if 'name' in data:
                    self.folder_manager.rename_folder(folder_id, data['name'])
                if 'parent_id' in data:
                    self.folder_manager.move_folder(folder_id, data.get('parent_id'))
                if 'order' in data:
                    self.folder_manager.reorder_folder(folder_id, data['order'])
                # Workspace fields
                ws_updates = {}
                if 'goal' in data:
                    ws_updates['goal'] = data['goal']
                if 'policy' in data:
                    ws_updates['policy'] = data['policy']
                if ws_updates:
                    self.folder_manager.update_workspace(folder_id, **ws_updates)
                return jsonify({"success": True})
            except ValueError as e:
                return jsonify({"success": False, "error": str(e)}), 400

        @app.route('/api/folders/<folder_id>', methods=['DELETE'])
        def delete_folder(folder_id: str):
            """Delete a folder."""
            data = request.get_json() or {}
            delete_contents = data.get('delete_contents', False)
            move_to_parent = data.get('move_to_parent', True)
            result = self.folder_manager.delete_folder(
                folder_id, delete_contents=delete_contents,
                move_to_parent=move_to_parent
            )
            if result.get("error"):
                return jsonify({"success": False, "error": result["error"]}), 404
            return jsonify({"success": True, **result})

        @app.route('/api/folders/<folder_id>/files', methods=['GET'])
        def get_folder_files(folder_id: str):
            """List files and chats in a folder."""
            contents = self.folder_manager.get_folder_contents(folder_id)
            # Enrich file info
            files_info = []
            for fn in contents["files"]:
                doc = get_document(fn)
                if doc:
                    files_info.append(doc)
                else:
                    files_info.append({"name": fn, "missing": True})
            # Enrich chat info
            chats_info = []
            for fn in contents["chats"]:
                path = os.path.join(self.chat_manager.chats_directory, fn)
                if os.path.exists(path):
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            chat_data = json.load(f)
                        title = chat_data.get("title", fn[:-5]) if isinstance(chat_data, dict) else fn[:-5]
                        hist = chat_data.get("chat_history", []) if isinstance(chat_data, dict) else chat_data
                        preview = ""
                        if hist:
                            first_user = next((m for m in hist if m.get("role") == "user"), None)
                            if first_user:
                                preview = first_user.get("content", "")[:100]
                        chats_info.append({
                            "filename": fn,
                            "title": title,
                            "preview": preview,
                            "messages": len(hist),
                        })
                    except Exception:
                        chats_info.append({"filename": fn, "title": fn[:-5], "preview": "", "messages": 0})
                else:
                    chats_info.append({"filename": fn, "title": fn[:-5], "missing": True})
            return jsonify({"files": files_info, "chats": chats_info})

        @app.route('/api/folders/<folder_id>/files', methods=['POST'])
        def assign_file_to_folder(folder_id: str):
            """Assign a file to a folder."""
            data = request.get_json() or {}
            filename = data.get('filename')
            if not filename:
                return jsonify({"success": False, "error": "filename required"}), 400
            ok = self.folder_manager.assign_file_to_folder(filename, folder_id)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/files/<path:filename>', methods=['DELETE'])
        def remove_file_from_folder(folder_id: str, filename: str):
            """Remove a file from a folder."""
            ok = self.folder_manager.remove_file_from_folder(filename)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/chats', methods=['POST'])
        def assign_chat_to_folder(folder_id: str):
            """Assign a chat to a folder."""
            data = request.get_json() or {}
            filename = data.get('filename')
            if not filename:
                return jsonify({"success": False, "error": "filename required"}), 400
            ok = self.folder_manager.assign_chat_to_folder(filename, folder_id)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/chats/<path:filename>', methods=['DELETE'])
        def remove_chat_from_folder(folder_id: str, filename: str):
            """Remove a chat from a folder."""
            ok = self.folder_manager.remove_chat_from_folder(filename)
            return jsonify({"success": ok})

        # ---- Folder context & memory ----

        @app.route('/api/folders/<folder_id>/context', methods=['GET'])
        def get_folder_context(folder_id: str):
            """Return the folder's saved prompts and memory notes."""
            folder = self.folder_manager._find_folder(folder_id)
            if not folder:
                return jsonify({"error": "not_found"}), 404
            return jsonify({
                "active_prompt_id": folder.get("active_prompt_id"),
                "saved_prompts": folder.get("saved_prompts", []),
                "memory_notes": folder.get("memory_notes", []),
                "prompt_branch_filename": folder.get("prompt_branch_filename"),
            })

        @app.route('/api/folders/<folder_id>/prompts', methods=['POST'])
        def save_folder_prompt(folder_id: str):
            """Save a new prompt for the folder."""
            data = request.get_json() or {}
            name = data.get("name", "").strip()
            content = data.get("content", "").strip()
            if not name or not content:
                return jsonify({"success": False,
                                "error": "name and content required"}), 400
            prompt = self.folder_manager.save_prompt(folder_id, name, content)
            if not prompt:
                return jsonify({"success": False,
                                "error": "folder not found"}), 404
            return jsonify({"success": True, "prompt": prompt})

        @app.route('/api/folders/<folder_id>/prompts/<prompt_id>',
                    methods=['PUT'])
        def rename_folder_prompt(folder_id: str, prompt_id: str):
            """Rename a saved prompt."""
            data = request.get_json() or {}
            name = data.get("name", "").strip()
            if not name:
                return jsonify({"success": False,
                                "error": "name required"}), 400
            ok = self.folder_manager.rename_prompt(folder_id, prompt_id, name)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/prompts/<prompt_id>',
                    methods=['DELETE'])
        def delete_folder_prompt(folder_id: str, prompt_id: str):
            """Delete a saved prompt."""
            ok = self.folder_manager.delete_prompt(folder_id, prompt_id)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/active-prompt', methods=['PUT'])
        def set_active_folder_prompt(folder_id: str):
            """Set which saved prompt is active (null to disable)."""
            data = request.get_json() or {}
            prompt_id = data.get("prompt_id")  # None is valid
            ok = self.folder_manager.set_active_prompt(folder_id, prompt_id)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/memory/add', methods=['POST'])
        def add_folder_memory_note(folder_id: str):
            """Add a note to the folder's memory."""
            data = request.get_json() or {}
            text = data.get("text", "").strip()
            source = data.get("source", "")
            if not text:
                return jsonify({"success": False, "error": "text required"}), 400
            ok = self.folder_manager.add_memory_note(folder_id, text, source)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/memory/note/<int:index>', methods=['DELETE'])
        def delete_folder_memory_note(folder_id: str, index: int):
            """Remove a memory note by index."""
            ok = self.folder_manager.remove_memory_note(folder_id, index)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/memory/clear', methods=['POST'])
        def clear_folder_memory(folder_id: str):
            """Clear all memory for a folder."""
            ok = self.folder_manager.clear_memory(folder_id)
            return jsonify({"success": ok})

        @app.route('/api/folders/<folder_id>/memory/note/<int:index>/compact',
                    methods=['POST'])
        def compact_memory_note(folder_id: str, index: int):
            """Use a fast LLM to compact a memory note."""
            folder = self.folder_manager._find_folder(folder_id)
            if not folder:
                return jsonify({"error": "not_found"}), 404
            notes = folder.get("memory_notes", [])
            if index < 0 or index >= len(notes):
                return jsonify({"error": "invalid index"}), 400
            original = notes[index]["text"]
            if len(original) < 40:
                return jsonify({"success": True, "text": original})

            title_provider = self.config.get("title_provider", "groq")
            title_model = self.config.get("title_model",
                                          "llama-3.1-8b-instant")
            stream_func = self._get_stream_func(title_provider)
            if not stream_func:
                return jsonify({"error": "no title provider"}), 500

            compact_prompt = (
                "Tighten the following text by removing filler words, "
                "redundant phrases, and unnecessary formatting while "
                "preserving ALL facts, details, and meaning. Do not "
                "summarize or omit information — just make it shorter "
                "without losing content. Output ONLY the tightened "
                "text, nothing else.\n\n" + original[:1500]
            )

            cfg = dict(self.config.config)
            api_key = self.config.get_api_key(title_provider)
            if api_key:
                cfg[f"{title_provider}_api_key"] = api_key
            if title_provider == "llamacpp":
                cfg["llamacpp_url"] = self._get_llamacpp_url()
            self._inject_endpoint_config(title_provider, cfg)
            cfg.update({
                "model": title_model,
                f"{title_provider}_model": title_model,
                f"{title_provider}_temperature": 0.2,
                f"{title_provider}_max_tokens": 300,
                f"{title_provider}_system_prompt": "",
                "temperature": 0.2,
                "system_prompt": "",
                "max_tokens": 300,
            })

            try:
                generated = ""
                for chunk in stream_func(
                    [{"role": "user", "content": compact_prompt}], cfg
                ):
                    generated += chunk
                compacted = generated.strip()
                if compacted:
                    notes[index]["text"] = compacted
                    self.folder_manager._save()
                    return jsonify({"success": True, "text": compacted})
                return jsonify({"success": False,
                                "error": "empty result"}), 500
            except Exception as e:
                return jsonify({"success": False,
                                "error": str(e)}), 500

        @app.route('/api/folders/<folder_id>/memory/note/<int:index>',
                    methods=['PUT'])
        def edit_folder_memory_note(folder_id: str, index: int):
            """Edit a memory note's text."""
            folder = self.folder_manager._find_folder(folder_id)
            if not folder:
                return jsonify({"error": "not_found"}), 404
            notes = folder.get("memory_notes", [])
            if index < 0 or index >= len(notes):
                return jsonify({"error": "invalid index"}), 400
            data = request.get_json() or {}
            text = data.get("text", "").strip()
            if not text:
                return jsonify({"success": False,
                                "error": "text required"}), 400
            notes[index]["text"] = text
            self.folder_manager._save()
            return jsonify({"success": True})

        # ---- Workspace + Edges (Phase 2) ----

        @app.route('/api/folders/<folder_id>/workspace', methods=['POST'])
        def make_workspace(folder_id):
            """Convert a folder into a workspace with lifecycle."""
            data = request.get_json() or {}
            folder = self.folder_manager.make_workspace(
                folder_id,
                goal=data.get('goal', ''),
                policy=data.get('policy')
            )
            if not folder:
                return jsonify({"error": "Folder not found"}), 404
            return jsonify({"success": True, "folder": folder})

        @app.route('/api/folders/<folder_id>/workspace', methods=['PUT'])
        def update_workspace(folder_id):
            """Update workspace goal/policy."""
            data = request.get_json() or {}
            folder = self.folder_manager.update_workspace(folder_id, **data)
            if not folder:
                return jsonify({"error": "Folder not found"}), 404
            return jsonify({"success": True, "folder": folder})

        @app.route('/api/folders/<folder_id>/status', methods=['POST'])
        def transition_folder_status(folder_id):
            """Transition workspace status (lifecycle enforced)."""
            data = request.get_json() or {}
            new_status = data.get('status')
            if not new_status:
                return jsonify({"error": "status required"}), 400
            try:
                folder = self.folder_manager.transition_status(folder_id, new_status)
                return jsonify({"success": True, "folder": folder})
            except ValueError as e:
                return jsonify({"error": str(e)}), 400

        @app.route('/api/folders/<folder_id>/transitions', methods=['GET'])
        def get_folder_transitions(folder_id):
            """Get valid status transitions for a workspace folder."""
            transitions = self.folder_manager.get_valid_transitions(folder_id)
            return jsonify({"transitions": transitions})

        @app.route('/api/edges', methods=['POST'])
        def create_edge():
            """Create an edge between entities (folders or chats)."""
            data = request.get_json() or {}
            from_id = data.get('from_id')
            to_id = data.get('to_id')
            edge_type = data.get('type')

            if not all([from_id, to_id, edge_type]):
                return jsonify({"error": "from_id, to_id, and type required"}), 400

            self.branch_db.add_edge(
                from_branch=from_id,
                to_branch=to_id,
                edge_type=edge_type,
                payload=data.get('payload')
            )
            return jsonify({"success": True}), 201

        @app.route('/api/edges', methods=['GET'])
        def list_edges():
            """Query edges with filters."""
            from_id = request.args.get('from_id')
            to_id = request.args.get('to_id')
            edge_type = request.args.get('type')
            edges = self.branch_db.list_edges(
                from_branch=from_id,
                to_branch=to_id,
                edge_type=edge_type
            )
            return jsonify({"edges": edges})

        @app.route('/api/edges/<int:edge_id>', methods=['DELETE'])
        def delete_edge(edge_id):
            """Delete an edge."""
            deleted = self.branch_db.delete_edge(edge_id)
            if not deleted:
                return jsonify({"error": "Edge not found"}), 404
            return jsonify({"success": True})

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

        # ---- URL Ingestion (Phase 7) ----
        @app.route('/api/context/url', methods=['POST'])
        def ingest_url():
            """Ingest a web URL as a document."""
            data = request.get_json() or {}
            url = data.get('url', '').strip()
            
            if not url:
                return jsonify({"success": False, "error": "URL is required"}), 400
            
            if not url.startswith(('http://', 'https://')):
                return jsonify({"success": False, "error": "URL must start with http:// or https://"}), 400
            
            try:
                from readers.url_reader import UrlReader
                
                # Extract text from URL
                text = UrlReader.extract_text(url)
                if not text or not text.strip():
                    return jsonify({"success": False, "error": "No text could be extracted from URL"}), 400
                
                # Create a filename from URL
                from urllib.parse import urlparse
                parsed = urlparse(url)
                name = f"{parsed.netloc}_{parsed.path.replace('/', '_')[:50]}.txt" or "web_page.txt"
                
                # Save as document
                ok, meta = save_document(name, text.encode('utf-8'))
                if ok:
                    # Store the source URL in metadata
                    meta['source_url'] = url
                return (jsonify({"success": ok, "document": meta}), 200 if ok else 400)
                
            except ImportError as e:
                return jsonify({"success": False, "error": f"URL ingestion requires: pip install requests beautifulsoup4. {str(e)}"}), 500
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        # ---- Reader Status (Phase 7) ----
        @app.route('/api/context/readers', methods=['GET'])
        def get_readers():
            """List available readers and supported file extensions."""
            readers = reader_registry.supported_extensions()
            return jsonify({"success": True, "readers": readers})

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

            stream_func = self._get_stream_func(provider)

            if not stream_func:
                return jsonify({"success": False, "error": f"Unknown provider: {provider}"}), 400

            # Build a merged config that pins the chosen model (and per-model settings)
            merged_cfg = dict(self.config.config)
            if provider == "llamacpp":
                merged_cfg["llamacpp_url"] = self._get_llamacpp_url()
            self._inject_endpoint_config(provider, merged_cfg)
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

        # ---- llama.cpp status (simple HTTP) ----
        @app.route('/api/llamacpp/status')
        def llamacpp_status():
            """Get llama.cpp server status via simple HTTP health check + /v1/models."""
            try:
                base = self._get_llamacpp_url().rstrip("/")
                loaded = []

                # Check /v1/models
                try:
                    rm = _local_session.get(f"{base}/v1/models", timeout=5)
                    if rm.status_code == 200:
                        for m in rm.json().get("data", []):
                            mid = m.get("id", "")
                            if mid:
                                loaded.append({"name": mid, "path": mid})
                except Exception:
                    pass

                # If no models from /v1/models, check /health
                if not loaded:
                    try:
                        rh = _local_session.get(f"{base}/health", timeout=5)
                        if rh.status_code == 200:
                            return jsonify({"success": True, "server_running": True, "loaded_models": []})
                    except Exception:
                        pass
                    return jsonify({"success": False, "server_running": False, "loaded_models": [],
                                    "message": "Cannot connect to llama.cpp server"})

                return jsonify({"success": True, "server_running": True, "loaded_models": loaded})
            except Exception as e:
                return jsonify({"success": False, "server_running": False, "loaded_models": [],
                                "message": str(e)})

        @app.route('/api/llamacpp/refresh', methods=['POST'])
        def llamacpp_refresh():
            """Refresh available models by querying the running llama-server."""
            try:
                from api_clients import get_available_llamacpp_models
                models = get_available_llamacpp_models(self.config.config)
                if models:
                    self.config.update_stored_models("llamacpp", models)
                    return jsonify({"success": True, "models": models})

                stored = self.config.get("stored_llamacpp_models", [])
                return jsonify({"success": True, "models": stored, "note": "Using cached models"})
            except Exception as e:
                return jsonify({"success": False, "error": str(e), "models": []}), 500

        @app.route('/api/llamacpp/url', methods=['GET'])
        def llamacpp_url_get():
            """Get current llama.cpp server URL."""
            return jsonify({
                "success": True,
                "llamacpp_url": self.config.get_llamacpp_url(),
            })

        @app.route('/api/llamacpp/url', methods=['POST'])
        def llamacpp_url_set():
            """Save llama.cpp server URL."""
            data = request.get_json() or {}
            url = (data.get("llamacpp_url") or "").strip()
            if url:
                self.config.set("llamacpp_url", url)
                self.config.save_config()
            return jsonify({"success": True})

        @app.route('/api/llamacpp/saved-urls', methods=['GET'])
        def llamacpp_saved_urls_get():
            """Get saved llama.cpp server URLs."""
            return jsonify({
                "success": True,
                "saved_urls": self.config.get_llamacpp_saved_urls(),
                "active_url": self.config.get_llamacpp_url(),
            })

        @app.route('/api/llamacpp/saved-urls', methods=['POST'])
        def llamacpp_saved_urls_set():
            """Save llama.cpp server URLs list."""
            data = request.get_json() or {}
            urls = data.get("saved_urls", [])
            self.config.set_llamacpp_saved_urls(urls)
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

                # Update folder mapping if the chat was in a folder
                folder_id = self.folder_manager.get_chat_folder(filename)
                if folder_id:
                    self.folder_manager.remove_chat_from_folder(filename)
                    self.folder_manager.assign_chat_to_folder(new_fn, folder_id)

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
            """
            Create a side chat (fork) from a message.
            Delegates to BranchManager.fork_branch() for DAG tracking.
            Also creates JSON file for backward compatibility.
            """
            data = request.get_json() or {}
            parent = data.get('parent_chat_id') or self.chat_manager.current_chat_file
            idx = int(data.get('parent_message_index', -1))
            selected_text = data.get('selected_text')
            name = data.get('name', '')  # Optional name for the fork

            if not parent or idx < 0:
                return jsonify({"success": False, "error": "parent_chat_id and parent_message_index required"}), 400

            # Load the parent chat to get its chat_id
            if self.chat_manager.current_chat_file != parent:
                self.chat_manager.load_chat(parent)

            # Get parent chat_id and root_chat_id
            parent_chat_id = self.chat_manager.current_chat.get("chat_id", parent)
            root_chat_id = self.chat_manager.current_chat.get("root_chat_id", parent_chat_id)

            # Create branch record via BranchManager (Phase 2)
            try:
                fork = self.branch_manager.fork_branch(
                    source_id=parent_chat_id,
                    at_message_index=idx,
                    name=name if name else None
                )
                fork_id = fork['id']
            except Exception as e:
                # Fallback: generate ID manually if branch not in DB yet
                fork_id = str(uuid.uuid4())

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

            # Generate timestamp for the side chat
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            side_fn = f"side_{fork_id}_{ts}.json"
            path = os.path.join(self.chat_manager.chats_directory, side_fn)

            # Add a field title to the saved JSON, initialize title as ""
            data_obj = {
                "chat_id": fork_id,
                "chat_history": side_msgs,
                "conversation_summary": "",
                "token_count": sum(estimate_tokens(m.get("content","")) for m in side_msgs),
                "parent_chat_id": parent_chat_id,
                "root_chat_id": root_chat_id,
                "title": name if name else "",  # Initialize with provided name or empty
                "branch_id": fork_id,  # Link to branch record
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data_obj, f, indent=2, ensure_ascii=False)

            return jsonify({
                "success": True,
                "side_chat_id": f"side_{fork_id}",
                "filename": side_fn,
                "branch_id": fork_id
            })

        # --- Chat: cancel generation (used by the red Cancel button) ---
        @app.route('/api/chat/cancel', methods=['POST'])
        def cancel_route():
            data = request.get_json() or {}
            message_id = data.get('message_id')
            if message_id:
                _cancel_generation(message_id)
            return jsonify({"success": True})

        # --- System Prompts Management ---

        def _prompts_dir(self_=None):
            return os.path.join(os.path.dirname(__file__), 'prompts')

        def _read_jsonl(path):
            """Read a JSONL file and return list of parsed objects."""
            items = []
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                items.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
            return items

        def _write_jsonl(path, items):
            """Write a list of objects to a JSONL file."""
            with open(path, 'w', encoding='utf-8') as f:
                for item in items:
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')

        def _default_prompts_path():
            return os.path.join(_prompts_dir(), 'default_prompts.jsonl')

        def _custom_prompts_path():
            return os.path.join(_prompts_dir(), 'custom_prompts.jsonl')

        def _load_all_prompts():
            """Load defaults then overlay with custom prompts (custom overrides by id)."""
            defaults = _read_jsonl(_default_prompts_path())
            customs = _read_jsonl(_custom_prompts_path())
            # Build ordered dict: defaults first, customs override or append
            by_id = {}
            order = []
            for p in defaults:
                pid = p.get('id')
                by_id[pid] = p
                order.append(pid)
            for p in customs:
                pid = p.get('id')
                if pid not in by_id:
                    order.append(pid)
                by_id[pid] = p
            return [by_id[pid] for pid in order]

        @app.route('/api/prompts', methods=['GET'])
        def get_prompts():
            """Get all system prompts (defaults + custom)"""
            try:
                prompts = _load_all_prompts()
                return jsonify({"success": True, "prompts": prompts})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/prompts', methods=['POST'])
        def create_prompt():
            """Create a new custom system prompt"""
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

                # Check if ID already exists in either file
                all_prompts = _load_all_prompts()
                if any(p.get('id') == prompt_id for p in all_prompts):
                    return jsonify({"success": False, "error": "A prompt with this ID already exists"}), 400

                # Append to custom prompts
                new_prompt = {"id": prompt_id, "title": title, "body": body}
                with open(_custom_prompts_path(), 'a', encoding='utf-8') as f:
                    f.write(json.dumps(new_prompt, ensure_ascii=False) + '\n')

                return jsonify({"success": True, "prompt": new_prompt})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/prompts/<prompt_id>', methods=['PUT'])
        def update_prompt(prompt_id):
            """Update an existing system prompt (writes override to custom file)"""
            try:
                data = request.get_json() or {}
                title = data.get('title', '').strip()
                body = data.get('body', '').strip()

                if not title or not body:
                    return jsonify({"success": False, "error": "title and body are required"}), 400

                # Check prompt exists somewhere
                all_prompts = _load_all_prompts()
                if not any(p.get('id') == prompt_id for p in all_prompts):
                    return jsonify({"success": False, "error": "Prompt not found"}), 404

                # Update or add override in custom file
                customs = _read_jsonl(_custom_prompts_path())
                found_in_custom = False
                for p in customs:
                    if p.get('id') == prompt_id:
                        p['title'] = title
                        p['body'] = body
                        found_in_custom = True
                        break

                if not found_in_custom:
                    # Overriding a default — add to custom file
                    customs.append({"id": prompt_id, "title": title, "body": body})

                _write_jsonl(_custom_prompts_path(), customs)
                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        @app.route('/api/prompts/<prompt_id>', methods=['DELETE'])
        def delete_prompt(prompt_id):
            """Delete a system prompt (only from custom file)"""
            try:
                # Check if it's a default prompt
                defaults = _read_jsonl(_default_prompts_path())
                if any(p.get('id') == prompt_id for p in defaults):
                    return jsonify({"success": False, "error": "Cannot delete a default prompt"}), 400

                customs = _read_jsonl(_custom_prompts_path())
                new_customs = [p for p in customs if p.get('id') != prompt_id]

                if len(new_customs) == len(customs):
                    return jsonify({"success": False, "error": "Prompt not found"}), 404

                _write_jsonl(_custom_prompts_path(), new_customs)
                return jsonify({"success": True})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

    
    
    # --------------- helpers ---------------

    def _get_llamacpp_url(self) -> str:
        """Return the llama.cpp server URL (env var > config > default)."""
        return self.config.get_llamacpp_url().rstrip("/")

    def _get_llamacpp_live_model(self):
        """Query the running llama-server for the currently loaded model.
        Returns (model_list, current_model) — both derived from the server."""
        try:
            base = self._get_llamacpp_url()
            resp = _local_session.get(f"{base}/v1/models", timeout=5)
            if resp.status_code == 200:
                ids = [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
                if ids:
                    return ids, ids[0]
        except Exception:
            pass
        return [], ""

    # ---- Folder context helpers ----

    def _build_folder_context(self, folder_id: str) -> list:
        """Build system messages for folder context injection."""
        messages = []

        # Active folder prompt (not wrapped - user-authored instructions)
        prompt_content = self.folder_manager.get_active_prompt_content(folder_id)
        if prompt_content:
            # Truncate large folder prompts
            prompt_content = truncate_head_tail(prompt_content, 10000, source_name="folder prompt")
            messages.append({
                "role": "system",
                "content": f"[Folder Context]\n{prompt_content}",
            })

        # Memory notes (wrapped as external content)
        memory = self.folder_manager.get_folder_memory(folder_id)
        if memory["notes"]:
            # Truncate each note and wrap as external content
            wrapped_notes = []
            for n in memory["notes"]:
                note_text = truncate_head_tail(n["text"], 2000, source_name="memory note")
                wrapped = wrap_external_content(note_text, "folder memory")
                wrapped_notes.append(wrapped)
            messages.append({
                "role": "system",
                "content": "\n".join(wrapped_notes),
            })

        return messages


    def get_provider_models(self, provider: str) -> List[str]:
        # Highest priority: user-defined custom list (if present)
        custom = self.config.get(f"custom_{provider}_models", [])
        if custom:
            return custom

        # Use ConfigManager's provider lists (already cached/stored)
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
