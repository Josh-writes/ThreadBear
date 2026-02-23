"""
Configuration management for the AI Chat App (env-first with sane fallbacks)
"""
from __future__ import annotations
import json
import os
from typing import Dict, Any, List


# ---------------- Defaults ----------------

DEFAULT_CONTEXT_WINDOWS: Dict[str, int] = {
    "ollama": 8192,
    "groq": 131072,
    "google": 1048576,
    "mistral": 32768,
    "openrouter": 8192,
    "llamacpp": 8192,
}

DEFAULT_CONFIG: Dict[str, Any] = {
    # API keys (fallbacks – env is preferred)
    "groq_api_key": "your_groq_api_key_here",
    "google_api_key": "your_google_api_key_here",
    "mistral_api_key": "your_mistral_api_key_here",
    "openrouter_api_key": "your_openrouter_api_key_here",

    # Ollama defaults
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "gemma2:2b",
    "ollama_temperature": 0.7,
    "ollama_system_prompt": "",
    "ollama_max_tokens": 8192,

    # Groq
    "groq_model": "llama-3.3-70b-versatile",
    "groq_temperature": 0.7,
    "groq_system_prompt": "",
    "groq_max_tokens": 131072,
    "groq_base_url": "https://api.groq.com/openai/v1",

    # Google
    "google_model": "gemini-1.5-pro",
    "google_temperature": 0.7,
    "google_system_prompt": "",
    "google_max_tokens": 4096,

    # Mistral
    "mistral_model": "mistral-medium-latest",
    "mistral_temperature": 0.7,
    "mistral_system_prompt": "",
    "mistral_max_tokens": 32768,

    # OpenRouter
    "openrouter_model": "meta-llama/llama-3.2-3b-instruct:free",
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
    "stored_ollama_models": [
        "gemma2:2b", "gemma2:9b", "llama3.1:8b", "llama3.2:3b",
        "mistral:latest", "phi3:3.8b", "qwen2.5:7b"
    ],
    "stored_groq_models": [],
    "stored_google_models": [],
    "stored_mistral_models": [],
    "stored_openrouter_models": [],

    # llama.cpp remote server
    "llamacpp_url": "http://127.0.0.1:8080",
    "llamacpp_model_dir": "/home/josh/models",
    "llamacpp_model": "model",
    "llamacpp_temperature": 0.7,
    "llamacpp_system_prompt": "",
    "llamacpp_max_tokens": 8192,
    "stored_llamacpp_models": [],

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
        return ["ollama", "groq", "google", "mistral", "openrouter", "llamacpp"]

    def get_models_for_provider(self, provider: str) -> List[str]:
        defaults = {
            "ollama": [
                "gemma2:2b", "gemma2:9b", "llama3.1:8b", "llama3.2:3b",
                "mistral:latest", "phi3:3.8b", "qwen2.5:7b"
            ],
            "groq": [
                "llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                "mixtral-8x7b-32768", "gemma2-9b-it"
            ],
            "google": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash"],
            "mistral": ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest", "open-mistral-nemo"],
            "openrouter": ["meta-llama/llama-3.2-3b-instruct:free", "mistralai/mistral-7b-instruct:free"],
            "llamacpp": ["model"],  # Placeholder - will be populated from server
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
        """
        env_map = {
            "groq": "GROQ_API_KEY",
            "google": "GOOGLE_API_KEY", 
            "mistral": "MISTRAL_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }
        env = env_map.get(provider)
        if env:
            v = os.getenv(env)
            if v and v.strip():  # Make sure it's not empty
                return v.strip()
        
        # Fallback to config.json (should be removed for security)
        config_key = self.config.get(f"{provider}_api_key", "")
        if config_key and config_key != f"your_{provider}_api_key_here":
            print(f"WARNING: Using API key from config.json for {provider}. Consider using environment variable {env} instead.")
            return config_key
        
        return ""

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
        return DEFAULT_CONTEXT_WINDOWS.get(provider, 8192)

    def reload_api_keys_from_env(self) -> None:
        """
        Check environment variables for API keys but DO NOT save them to config.json.
        This method is now deprecated - use get_api_key() which reads from env every time.
        """
        # This method no longer saves keys to config.json for security
        # API keys are read from environment variables on-demand via get_api_key()
        pass
