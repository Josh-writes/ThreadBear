"""Provider catalog helpers shared across ThreadBear front-ends."""

from __future__ import annotations

from typing import Dict, Any

BUILTIN_PROVIDERS = ["groq", "google", "mistral", "openrouter", "llamacpp"]

# name slug -> endpoint metadata
KNOWN_OPENAI_COMPAT_PROVIDERS: Dict[str, Dict[str, Any]] = {
    "cerebras": {"base_url": "https://api.cerebras.ai/v1", "context_window": 131072},
    "together": {"base_url": "https://api.together.xyz/v1", "context_window": 131072},
    "togetherai": {"base_url": "https://api.together.xyz/v1", "context_window": 131072},
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "context_window": 65536},
    "xai": {"base_url": "https://api.x.ai/v1", "context_window": 131072},
    "fireworks": {"base_url": "https://api.fireworks.ai/inference/v1", "context_window": 131072},
    "perplexity": {"base_url": "https://api.perplexity.ai", "context_window": 131072},
    "nvidia": {"base_url": "https://integrate.api.nvidia.com/v1", "context_window": 32768},
    "ollama": {"base_url": "http://localhost:11434/v1", "context_window": 8192},
    "lmstudio": {"base_url": "http://localhost:1234/v1", "context_window": 8192},
}


def inject_endpoint_config(provider: str, merged_cfg: Dict[str, Any], config_manager) -> None:
    """Inject base_url + API key for known/custom OpenAI-compatible providers."""
    if provider in KNOWN_OPENAI_COMPAT_PROVIDERS:
        ep = KNOWN_OPENAI_COMPAT_PROVIDERS[provider]
        merged_cfg["_endpoint_base_url"] = ep["base_url"]
        merged_cfg["_endpoint_api_key"] = config_manager.get_api_key(provider)
        merged_cfg["_endpoint_provider"] = provider

    endpoints = config_manager.get("custom_endpoints", {})
    if provider in endpoints:
        ep = endpoints[provider]
        merged_cfg["_endpoint_base_url"] = ep["base_url"]
        merged_cfg["_endpoint_api_key"] = config_manager.get_api_key(provider)
        merged_cfg["_endpoint_provider"] = provider
