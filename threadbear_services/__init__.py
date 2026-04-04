"""Shared application services used by both Flask and CLI surfaces."""

from .providers import (
    BUILTIN_PROVIDERS,
    KNOWN_OPENAI_COMPAT_PROVIDERS,
    get_known_providers_with_api_keys,
    inject_endpoint_config,
)
from .text_utils import truncate_text_head_tail, truncate_tool_result

__all__ = [
    "BUILTIN_PROVIDERS",
    "KNOWN_OPENAI_COMPAT_PROVIDERS",
    "get_known_providers_with_api_keys",
    "inject_endpoint_config",
    "truncate_text_head_tail",
    "truncate_tool_result",
]
