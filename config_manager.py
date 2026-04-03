"""
Configuration management for the AI Chat App (env-first with sane fallbacks)
"""
from __future__ import annotations
import json
import os
from typing import Dict, Any, List


# ---------------- Defaults ----------------

DEFAULT_CONTEXT_WINDOWS: Dict[str, int] = {
    "groq": 131072,
    "google": 1048576,
    "mistral": 32768,
    "openrouter": 8192,
    "llamacpp": 8192,
    "cerebras": 131072,
}

DEFAULT_CONFIG: Dict[str, Any] = {
    # API keys (fallbacks – env is preferred)
    "groq_api_key": "your_groq_api_key_here",
    "google_api_key": "your_google_api_key_here",
    "mistral_api_key": "your_mistral_api_key_here",
    "openrouter_api_key": "your_openrouter_api_key_here",

    # Groq
    "groq_model": "",
    "groq_temperature": 0.7,
    "groq_system_prompt": "",
    "groq_max_tokens": 131072,
    "groq_base_url": "https://api.groq.com/openai/v1",

    # Google
    "google_model": "",
    "google_temperature": 0.7,
    "google_system_prompt": "",
    "google_max_tokens": 4096,

    # Mistral
    "mistral_model": "",
    "mistral_temperature": 0.7,
    "mistral_system_prompt": "",
    "mistral_max_tokens": 32768,

    # OpenRouter
    "openrouter_model": "",
    "openrouter_temperature": 0.7,
    "openrouter_system_prompt": "",
    "openrouter_max_tokens": 8192,

    # UI/system
    "provider": "groq",
    "temperature": 0.7,
    "window_geometry": "",
    "system_theme": "light",

    # Documents
    "max_upload_mb": 10,
    "pdf_page_limit": 50,
    "max_context_doc_tokens": 50000,
    "doc_extract_timeout": 60,
    "allowed_file_types": [".txt", ".md", ".markdown", ".pdf", ".docx"],

    # Caches / recents
    "recent_groq_models": [],
    "recent_google_models": [],
    "recent_mistral_models": [],
    "recent_openrouter_models": [],
    "stored_groq_models": [],
    "stored_google_models": [],
    "stored_mistral_models": [],
    "stored_openrouter_models": [],

    # llama.cpp server
    "llamacpp_url": "http://localhost:8080",
    "llamacpp_saved_urls": [],
    "llamacpp_model": "",
    "llamacpp_temperature": 0.7,
    "llamacpp_system_prompt": "",
    "llamacpp_max_tokens": 8192,
    "stored_llamacpp_models": [],

    # Auto-title generation
    "title_provider": "groq",
    "title_model": "llama-3.1-8b-instant",

    # Tool system (Phase 3)
    "tools_enabled": False,                    # Master switch
    "groq_tools_enabled": False,               # Per-provider
    "google_tools_enabled": False,
    "mistral_tools_enabled": False,
    "openrouter_tools_enabled": False,
    "llamacpp_tools_enabled": False,
    "max_tool_iterations": 5,                  # Max tool loops per request
    "tool_execution_timeout": 30,              # Default per-tool timeout (seconds)
    "require_tool_confirmation": True,         # Ask user before destructive tools
    "blocked_commands": ['rm -rf', 'del /f /s', 'format', 'shutdown'],
    "tool_workspace": None,                    # Restrict file access (None = unrestricted)
    "tool_os": "windows",                       # OS hint for tool commands (windows/linux/macos)

    # Custom OpenAI-compatible endpoints
    "custom_endpoints": {},
    # Structure: {
    #   "nvidia": {
    #     "name": "NVIDIA NIM",
    #     "base_url": "https://integrate.api.nvidia.com/v1",
    #     "api_key_env": "NVIDIA_API_KEY",
    #     "api_key": "",
    #     "default_model": "",
    #     "context_window": 32768,
    #   },
    # }

    # Misc
    "temp_mode_warning": True,
}


# ---------------- Manager ----------------

class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.config: Dict[str, Any] = self.load_config()

    # ---------- IO ----------
    def load_config(self) -> Dict[str, Any]:
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            else:
                cfg = {}
        except Exception as e:
            print(f"Error loading config: {e}")
            cfg = {}

        merged = DEFAULT_CONFIG.copy()
        merged.update(cfg)
        
        # Migration: ensure model_settings key exists
        if "model_settings" not in merged:
            merged["model_settings"] = {}
        
        return merged

    def save_config(self) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    # ---------- Accessors ----------
    def get(self, key: str, default=None):
        return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.config[key] = value

    def update(self, updates: Dict[str, Any]) -> None:
        self.config.update(updates)

    def get_all_providers(self) -> List[str]:
        builtin = ["groq", "google", "mistral", "openrouter", "llamacpp"]
        custom = list(self.config.get("custom_endpoints", {}).keys())
        return builtin + custom

    def get_models_for_provider(self, provider: str) -> List[str]:
        defaults = {
            "groq": [],
            "google": [],
            "mistral": [],
            "openrouter": [],
            "llamacpp": [],
        }

        # explicit override
        custom = self.config.get(f"custom_{provider}_models")
        if isinstance(custom, list) and custom:
            return custom

        # last seen from live queries
        stored = self.config.get(f"stored_{provider}_models") or []
        if stored:
            return stored

        # recently used by user
        recent = self.config.get(f"recent_{provider}_models") or []
        if recent:
            return recent

        return defaults.get(provider, [])

    # ---------- Recent / Stored ----------
    def add_recent_model(self, provider: str, model: str) -> None:
        key = f"recent_{provider}_models"
        models = list(self.config.get(key, []))
        if model in models:
            models.remove(model)
        models.insert(0, model)
        self.config[key] = models[:10]
        self.save_config()

    def update_stored_models(self, provider: str, models: List[str]) -> None:
        self.config[f"stored_{provider}_models"] = list(models)
        self.save_config()

    # ---------- API keys ----------
    def get_api_key(self, provider: str) -> str:
        """
        Get API key for provider, prioritizing environment variables for security.
        Always reads from environment first, then falls back to config.json (not recommended).
        Supports custom endpoints via their api_key_env / api_key fields.
        """
        env_map = {
            "groq": "GROQ_API_KEY",
            "google": "GOOGLE_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "cerebras": "CEREBRAS_API_KEY",
        }
        env = env_map.get(provider)
        if env:
            v = os.getenv(env)
            if v and v.strip():
                return v.strip()

        # Check custom endpoints
        endpoints = self.config.get("custom_endpoints", {})
        if provider in endpoints:
            ep = endpoints[provider]
            env_var = ep.get("api_key_env", "")
            if env_var:
                v = os.getenv(env_var, "")
                if v and v.strip():
                    return v.strip()
            direct = ep.get("api_key", "")
            if direct and direct.strip():
                return direct.strip()
            return ""

        # Fallback to config.json (should be removed for security)
        config_key = self.config.get(f"{provider}_api_key", "")
        if config_key and config_key != f"your_{provider}_api_key_here":
            print(f"WARNING: Using API key from config.json for {provider}. Consider using environment variable {env} instead.")
            return config_key

        return ""

    # ---------- llama.cpp URL ----------
    def get_llamacpp_url(self) -> str:
        """Get llama.cpp server URL from config."""
        return self.config.get("llamacpp_url", "http://localhost:8080")

    def get_llamacpp_saved_urls(self) -> List[Dict[str, str]]:
        """Get saved llama.cpp server URLs."""
        return self.config.get("llamacpp_saved_urls", [])

    def set_llamacpp_saved_urls(self, urls: List[Dict[str, str]]) -> None:
        """Set saved llama.cpp server URLs."""
        self.config["llamacpp_saved_urls"] = urls
        self.save_config()

    def migrate_llamacpp_saved_urls(self) -> None:
        """One-time migration: seed saved_urls from current config."""
        saved = self.config.get("llamacpp_saved_urls", [])
        if saved:
            return  # Already migrated

        urls = []
        current = self.config.get("llamacpp_url", "http://localhost:8080")
        if current:
            urls.append({"label": "Default", "url": current})

        # Check for zerotier URL if it exists
        zt = self.config.get("llamacpp_zerotier_url", "")
        if zt and zt != current:
            urls.append({"label": "ZeroTier", "url": zt})

        if urls:
            self.config["llamacpp_saved_urls"] = urls
            self.save_config()

    # ---------- Per-model settings ----------
    def _ensure_model_settings(self) -> None:
        if "model_settings" not in self.config or not isinstance(self.config["model_settings"], dict):
            self.config["model_settings"] = {}

    def get_model_settings(self, provider: str, model: str) -> Dict[str, Any]:
        self._ensure_model_settings()
        return self.config["model_settings"].get(provider, {}).get(model, {})

    def get_all_model_settings_for_provider(self, provider: str) -> Dict[str, Any]:
        self._ensure_model_settings()
        return self.config["model_settings"].get(provider, {})

    def set_model_settings(self, provider: str, model: str, updates: Dict[str, Any]) -> None:
        self._ensure_model_settings()
        prov = self.config["model_settings"].setdefault(provider, {})
        cur = prov.setdefault(model, {})
        # keep only allowed keys
        allow = {"max_tokens", "temperature", "top_p", "top_k", "system_prompt", "context_window"}
        for k,v in updates.items():
            if k in allow:
                cur[k] = v
        self.save_config()

    def get_context_window(self, provider: str, model: str) -> int:
        self._ensure_model_settings()
        ms = self.config["model_settings"].get(provider, {}).get(model, {})
        if "context_window" in ms and ms["context_window"]:
            return int(ms["context_window"])
        # Check cached catalog for this provider
        catalog = self.config.get(f"{provider}_catalog", [])
        for entry in catalog:
            if entry.get("id") == model:
                ctx = entry.get("context_length", 0)
                if ctx > 0:
                    return int(ctx)
        # Check custom endpoint default
        ep = self.config.get("custom_endpoints", {}).get(provider, {})
        if ep.get("context_window"):
            return int(ep["context_window"])
        return DEFAULT_CONTEXT_WINDOWS.get(provider, 8192)

    def get_system_prompt(self, provider: str, model: str) -> str:
        """
        Get system prompt for a provider/model, with template fallback.

        Priority:
        1. Per-model custom system prompt (from model_settings)
        2. Per-provider system prompt (from config)
        3. Default template for provider
        4. Default template for model family
        """
        from prompt_templates import get_default_prompt

        # Check per-model settings first
        self._ensure_model_settings()
        model_settings = self.config["model_settings"].get(provider, {}).get(model, {})
        if "system_prompt" in model_settings and model_settings["system_prompt"]:
            return model_settings["system_prompt"]

        # Check per-provider config
        provider_prompt = self.config.get(f"{provider}_system_prompt", "")
        if provider_prompt and provider_prompt.strip():
            return provider_prompt

        # Fall back to default template
        return get_default_prompt(provider, model)

    def get_tool_config(self, provider: str) -> Dict[str, Any]:
        """Get tool configuration for a provider."""
        return {
            'enabled': self.config.get(f'{provider}_tools_enabled', False),
            'max_iterations': self.config.get('max_tool_iterations', 5),
            'timeout': self.config.get('tool_execution_timeout', 30),
            'require_confirmation': self.config.get('require_tool_confirmation', True),
            'blocked_commands': self.config.get('blocked_commands', []),
            'workspace': self.config.get('tool_workspace'),
        }

    # ---------- Custom endpoints ----------
    def get_custom_endpoints(self) -> Dict[str, Any]:
        return self.config.get("custom_endpoints", {})

    def get_endpoint_config(self, endpoint_id: str) -> Dict[str, Any]:
        return self.config.get("custom_endpoints", {}).get(endpoint_id, {})

    def save_endpoint(self, endpoint_id: str, endpoint_cfg: Dict[str, Any]) -> None:
        if "custom_endpoints" not in self.config:
            self.config["custom_endpoints"] = {}
        self.config["custom_endpoints"][endpoint_id] = endpoint_cfg
        self.save_config()

    def delete_endpoint(self, endpoint_id: str) -> bool:
        endpoints = self.config.get("custom_endpoints", {})
        if endpoint_id in endpoints:
            del endpoints[endpoint_id]
            # Clean up associated config keys
            for suffix in ["_model", "_temperature", "_system_prompt", "_max_tokens",
                           "_tools_enabled", "_catalog"]:
                self.config.pop(f"{endpoint_id}{suffix}", None)
            self.config.pop(f"stored_{endpoint_id}_models", None)
            self.config.pop(f"recent_{endpoint_id}_models", None)
            self.config.pop(f"custom_{endpoint_id}_models", None)
            # Clean up model_settings for this provider
            if "model_settings" in self.config and endpoint_id in self.config["model_settings"]:
                del self.config["model_settings"][endpoint_id]
            self.save_config()
            return True
        return False

    def reload_api_keys_from_env(self) -> None:
        """
        Check environment variables for API keys but DO NOT save them to config.json.
        This method is now deprecated - use get_api_key() which reads from env every time.
        """
        # This method no longer saves keys to config.json for security
        # API keys are read from environment variables on-demand via get_api_key()
        pass
