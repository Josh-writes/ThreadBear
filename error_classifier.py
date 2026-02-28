"""
Error classification for LLM API responses.

Classifies HTTP errors from providers into user-friendly categories
and generates actionable messages.
"""
from __future__ import annotations
import re
from enum import Enum
from typing import Optional


class LLMApiError(Exception):
    """Raised by API stream functions on non-200 responses."""

    def __init__(self, status_code: int, response_text: str, provider: str):
        self.status_code = status_code
        self.response_text = response_text
        self.provider = provider
        super().__init__(f"{provider} API error {status_code}: {response_text[:200]}")


class ErrorClass(Enum):
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    AUTH = "auth"
    BILLING = "billing"
    TIMEOUT = "timeout"
    FORMAT = "format"
    UNKNOWN = "unknown"


# Patterns checked against lowercased response text
_PATTERNS = [
    (ErrorClass.CONTEXT_OVERFLOW, [
        r"context.?length", r"token.?limit", r"too.?many.?tokens",
        r"maximum.?context", r"input.?too.?long", r"exceeds.*max.*length",
        r"content_length_exceeded", r"max_tokens",
    ]),
    (ErrorClass.RATE_LIMIT, [
        r"rate.?limit", r"too.?many.?requests", r"quota.?exceeded",
        r"throttl", r"retry.?after",
    ]),
    (ErrorClass.AUTH, [
        r"auth", r"api.?key", r"invalid.?key", r"unauthorized",
        r"forbidden", r"permission", r"access.?denied",
    ]),
    (ErrorClass.BILLING, [
        r"billing", r"payment", r"insufficient.?funds", r"credits",
        r"subscription", r"plan.?limit",
    ]),
    (ErrorClass.FORMAT, [
        r"invalid.?request", r"malformed", r"bad.?request",
        r"validation.?error", r"schema",
    ]),
]


def classify_error(status_code: int, response_text: str) -> ErrorClass:
    """Classify an API error by status code and response body."""
    lower = response_text.lower()

    # Status-code shortcuts
    if status_code == 429:
        return ErrorClass.RATE_LIMIT
    if status_code in (401, 403):
        return ErrorClass.AUTH
    if status_code == 408:
        return ErrorClass.TIMEOUT

    # Pattern matching on response body
    for error_class, patterns in _PATTERNS:
        for pat in patterns:
            if re.search(pat, lower):
                return error_class

    return ErrorClass.UNKNOWN


_FRIENDLY = {
    ErrorClass.RATE_LIMIT: (
        "Rate limited by {provider}. Please wait a moment and try again."
    ),
    ErrorClass.CONTEXT_OVERFLOW: (
        "The conversation is too long for {provider}'s context window. "
        "Try clearing older messages or switching to a model with a larger context."
    ),
    ErrorClass.AUTH: (
        "Authentication failed for {provider}. "
        "Please check your API key in Settings."
    ),
    ErrorClass.BILLING: (
        "{provider} rejected the request due to a billing or quota issue. "
        "Check your account balance or plan limits."
    ),
    ErrorClass.TIMEOUT: (
        "The request to {provider} timed out. The server may be overloaded — "
        "try again shortly."
    ),
    ErrorClass.FORMAT: (
        "{provider} rejected the request format. "
        "This may be a bug — please report it."
    ),
    ErrorClass.UNKNOWN: (
        "{provider} returned an error (HTTP {status}). "
        "Details: {detail}"
    ),
}


def friendly_message(error_class: ErrorClass, provider: str,
                     status_code: int = 0,
                     response_text: str = "") -> str:
    """Return a user-friendly error message."""
    template = _FRIENDLY.get(error_class, _FRIENDLY[ErrorClass.UNKNOWN])
    detail = response_text[:200] if response_text else "no details"
    return template.format(
        provider=provider.capitalize(),
        status=status_code,
        detail=detail,
    )
