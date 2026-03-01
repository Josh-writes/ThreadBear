"""
Cost tracking for ThreadBear.

Calculates API costs based on token usage and provider pricing.
Uses decimal.Decimal for precision to avoid floating-point errors.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional, List


# Hardcoded pricing tables (per 1M tokens, in USD)
# Source: Provider documentation as of early 2025
PRICING = {
    # Groq (per 1M tokens)
    "groq": {
        "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
        "llama-3.3-70b-specdec": {"input": 0.59, "output": 0.79},
        "llama-3.1-70b-versatile": {"input": 0.59, "output": 0.79},
        "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
        "llama3-70b-8192": {"input": 0.59, "output": 0.79},
        "llama3-8b-8192": {"input": 0.05, "output": 0.08},
        "mixtral-8x7b-32768": {"input": 0.24, "output": 0.24},
        "gemma2-9b-it": {"input": 0.20, "output": 0.20},
    },
    # Google Gemini (per 1M tokens)
    "google": {
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
        "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
        "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
        # With long context (>128K), prices increase
        "gemini-1.5-flash-long": {"input": 0.15, "output": 0.60},
        "gemini-1.5-pro-long": {"input": 2.50, "output": 10.00},
    },
    # Mistral (per 1M tokens)
    "mistral": {
        "mistral-large-latest": {"input": 2.0, "output": 6.0},
        "mistral-large-2411": {"input": 2.0, "output": 6.0},
        "mistral-small-latest": {"input": 0.2, "output": 0.6},
        "mistral-small-2409": {"input": 0.2, "output": 0.6},
        "pixtral-large-latest": {"input": 2.0, "output": 6.0},
        "codestral-latest": {"input": 0.3, "output": 0.9},
        "ministral-8b-latest": {"input": 0.1, "output": 0.1},
        "ministral-3b-latest": {"input": 0.04, "output": 0.04},
        "open-mistral-nemo": {"input": 0.15, "output": 0.15},
        "open-mistral-7b": {"input": 0.25, "output": 0.25},
        "open-mixtral-8x7b": {"input": 0.7, "output": 0.7},
        "open-mixtral-8x22b": {"input": 2.0, "output": 6.0},
    },
    # OpenRouter (per 1M tokens) - base prices, actual may vary
    "openrouter": {
        # OpenAI
        "openai/gpt-4o": {"input": 2.5, "output": 10.0},
        "openai/gpt-4o-mini": {"input": 0.15, "output": 0.6},
        "openai/gpt-4-turbo": {"input": 10.0, "output": 30.0},
        "openai/gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
        "openai/o1-preview": {"input": 15.0, "output": 60.0},
        "openai/o1-mini": {"input": 3.0, "output": 12.0},
        # Anthropic
        "anthropic/claude-3.5-sonnet": {"input": 3.0, "output": 15.0},
        "anthropic/claude-3.5-haiku": {"input": 0.8, "output": 4.0},
        "anthropic/claude-3-opus": {"input": 15.0, "output": 75.0},
        # Meta
        "meta-llama/llama-3.3-70b-instruct": {"input": 0.59, "output": 0.79},
        "meta-llama/llama-3.1-70b-instruct": {"input": 0.59, "output": 0.79},
        "meta-llama/llama-3.1-8b-instruct": {"input": 0.05, "output": 0.08},
        # Google
        "google/gemini-pro-1.5": {"input": 1.25, "output": 5.0},
        "google/gemini-flash-1.5": {"input": 0.075, "output": 0.3},
        # Mistral
        "mistralai/mistral-large": {"input": 2.0, "output": 6.0},
        "mistralai/mistral-small": {"input": 0.2, "output": 0.6},
        # DeepSeek
        "deepseek/deepseek-chat": {"input": 0.14, "output": 0.28},
        "deepseek/deepseek-coder": {"input": 0.14, "output": 0.28},
    },
    # llama.cpp (local) - no API cost, but can track electricity if desired
    "llamacpp": {
        # Local models have no API cost
        # User can optionally configure electricity cost per token
        "_default": {"input": 0.0, "output": 0.0},
    },
}

# Default fallback pricing (per 1M tokens)
DEFAULT_PRICING = {"input": 1.0, "output": 3.0}


class CostTracker:
    """Track and calculate API costs for LLM usage."""

    def __init__(self):
        self.pricing = PRICING

    def _get_model_pricing(
        self, provider: str, model: str
    ) -> Dict[str, float]:
        """Get pricing for a specific model."""
        provider_pricing = self.pricing.get(provider, {})

        # Try exact model match first
        if model in provider_pricing:
            return provider_pricing[model]

        # Try partial matches (e.g., "gemini-2.0-flash-exp" matches "gemini-2.0-flash")
        for known_model, pricing in provider_pricing.items():
            if known_model in model or model in known_model:
                return pricing

        # Use default for provider
        if "_default" in provider_pricing:
            return provider_pricing["_default"]

        return DEFAULT_PRICING

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> Decimal:
        """
        Calculate cost for a single API call.

        Args:
            provider: Provider name (groq, google, mistral, openrouter, llamacpp)
            model: Model name/identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cache_read: Cached tokens read (some providers discount these)
            cache_write: Cached tokens written (some providers charge for this)

        Returns:
            Cost in USD as Decimal for precision
        """
        pricing = self._get_model_pricing(provider, model)

        # Convert to Decimal for precision
        input_rate = Decimal(str(pricing.get("input", 0))) / Decimal("1000000")
        output_rate = Decimal(str(pricing.get("output", 0))) / Decimal("1000000")

        # Calculate base costs
        input_cost = input_rate * Decimal(str(input_tokens))
        output_cost = output_rate * Decimal(str(output_tokens))

        # Cache pricing (some providers offer discounts)
        # For now, assume cached reads are free, writes cost same as input
        if cache_read > 0:
            # Some providers charge less for cache hits
            cache_read_rate = input_rate * Decimal("0.5")  # 50% discount
            input_cost -= input_rate * Decimal(str(cache_read))
            input_cost += cache_read_rate * Decimal(str(cache_read))

        if cache_write > 0:
            cache_write_rate = input_rate  # Same as input
            input_cost += cache_write_rate * Decimal(str(cache_write))

        total = input_cost + output_cost
        return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    def get_session_cost(self, messages: List[Dict]) -> Decimal:
        """
        Calculate total cost for a chat session.

        Args:
            messages: List of message dicts with 'usage' and 'cost' fields

        Returns:
            Total cost in USD as Decimal
        """
        total = Decimal("0")
        for msg in messages:
            # Use stored cost if available
            if "cost" in msg and msg["cost"] is not None:
                total += Decimal(str(msg["cost"]))
            # Or calculate from usage
            elif "usage" in msg and msg.get("role") == "assistant":
                usage = msg["usage"]
                # Need model info - try to get from message
                model = msg.get("model", "")
                provider = msg.get("provider", "unknown")
                if provider != "unknown":
                    cost = self.calculate_cost(
                        provider,
                        model,
                        usage.get("input_tokens", 0),
                        usage.get("output_tokens", 0),
                    )
                    total += cost
        return total

    def get_total_cost(self, all_chats: List[Dict]) -> Decimal:
        """
        Calculate total cost across all chats.

        Args:
            all_chats: List of chat dicts, each with 'chat_history'

        Returns:
            Total cost in USD as Decimal
        """
        total = Decimal("0")
        for chat in all_chats:
            messages = chat.get("chat_history", [])
            total += self.get_session_cost(messages)
        return total

    def format_cost(self, cost: Decimal, precision: int = 4) -> str:
        """Format cost as USD string."""
        if cost < Decimal("0.01"):
            return f"${cost:.{precision}f}"
        else:
            return f"${cost:.3f}"


# Global instance for convenience
_cost_tracker = None


def get_cost_tracker() -> CostTracker:
    """Get or create the global CostTracker instance."""
    global _cost_tracker
    if _cost_tracker is None:
        _cost_tracker = CostTracker()
    return _cost_tracker


def calculate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_write: int = 0,
) -> float:
    """
    Convenience function to calculate cost.

    Returns cost as float for JSON serialization.
    """
    tracker = get_cost_tracker()
    cost = tracker.calculate_cost(
        provider, model, input_tokens, output_tokens, cache_read, cache_write
    )
    return float(cost)
