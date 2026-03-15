"""AgentWeave LLM pricing table.

Prices are in USD per 1 million tokens (input, output).
Update this table as providers change their pricing.

Override at runtime by setting AGENTWEAVE_PRICING_OVERRIDE to a JSON string:
  export AGENTWEAVE_PRICING_OVERRIDE='{"my-custom-model": [1.00, 5.00]}'

The override is merged on top of the default table, so you only need to
specify models you want to add or change.

Usage::
    from agentweave.pricing import compute_cost

    cost = compute_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
    # Returns 0.003 + 0.0075 = 0.0105
"""

from __future__ import annotations

import json
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Pricing table — USD per 1 million tokens (input_price, output_price)
# ---------------------------------------------------------------------------
# Keep this sorted by provider / model family for easy maintenance.
# Add new models here; they are automatically picked up by compute_cost().

_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic Claude
    "claude-opus-4-5": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-haiku-3-5": (0.80, 4.00),
    # Google Gemini
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    # OpenAI
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}

# Sentinel value returned when model is not in the pricing table.
# Distinguishable from zero (free/unknown) vs known cost.
UNKNOWN_COST: float = -1.0


def _load_pricing() -> dict[str, tuple[float, float]]:
    """Return the merged pricing table (defaults + env override)."""
    table = dict(_DEFAULT_PRICING)
    override_raw = os.getenv("AGENTWEAVE_PRICING_OVERRIDE", "").strip()
    if override_raw:
        try:
            overrides = json.loads(override_raw)
            for model, prices in overrides.items():
                if isinstance(prices, (list, tuple)) and len(prices) == 2:
                    table[model.lower()] = (float(prices[0]), float(prices[1]))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass  # Silently ignore malformed overrides
    return table


def _normalize_model_name(model: str) -> str:
    """Strip provider prefix and normalize to lowercase.

    Examples::
        "anthropic/claude-haiku-4-5" → "claude-haiku-4-5"
        "openai/gpt-4o"              → "gpt-4o"
        "Claude-Sonnet-4-6"          → "claude-sonnet-4-6"
    """
    model = model.lower().strip()
    # Strip provider prefix (e.g. "anthropic/", "openai/", "google/")
    if "/" in model:
        model = model.split("/", 1)[1]
    return model


def _find_model_pricing(
    model: str,
    table: dict[str, tuple[float, float]],
) -> Optional[tuple[float, float]]:
    """Look up model pricing with exact match first, then partial match.

    Returns ``None`` when no match is found.
    """
    normalized = _normalize_model_name(model)

    # 1. Exact match
    if normalized in table:
        return table[normalized]

    # 2. Partial match — useful for versioned models like "claude-sonnet-4-6-20250101"
    for key, prices in table.items():
        if key in normalized or normalized in key:
            return prices

    return None


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Compute USD cost for a single LLM call.

    Args:
        model: Model name (provider prefix is stripped automatically).
        input_tokens: Number of input/prompt tokens consumed.
        output_tokens: Number of output/completion tokens generated.

    Returns:
        Cost in USD as a float, or ``UNKNOWN_COST`` (-1.0) if the model
        is not found in the pricing table.

    Examples::
        compute_cost("claude-sonnet-4-6", 1_000_000, 0)     → 3.00
        compute_cost("anthropic/gpt-4o", 1_000_000, 0)      → 2.50
        compute_cost("unknown-model", 100, 50)               → -1.0
    """
    table = _load_pricing()
    prices = _find_model_pricing(model, table)
    if prices is None:
        return UNKNOWN_COST

    input_price_per_token = prices[0] / 1_000_000
    output_price_per_token = prices[1] / 1_000_000

    return (input_tokens * input_price_per_token) + (output_tokens * output_price_per_token)
