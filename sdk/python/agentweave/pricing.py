"""AgentWeave LLM pricing table.

Prices are in USD per 1 million tokens (input, output, cache_read, cache_write).
Update this table as providers change their pricing.

Override at runtime by setting AGENTWEAVE_PRICING_OVERRIDE to a JSON string:
  export AGENTWEAVE_PRICING_OVERRIDE='{"my-custom-model": [1.00, 5.00]}'

The override is merged on top of the default table, so you only need to
specify models you want to add or change.

Usage::
    from agentweave.pricing import compute_cost

    cost = compute_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
    # Returns 0.003 + 0.0075 = 0.0105

    cost = compute_cost(
        "claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=10000,
        cache_write_tokens=500,
    )
"""

from __future__ import annotations

import json
import os
from typing import Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Pricing table — USD per 1 million tokens
#
# Each entry is either a 2-tuple (input_price, output_price) or a 4-tuple
# (input_price, output_price, cache_read_price, cache_write_price).
# Models without prompt-caching use 2-tuples; Anthropic models use 4-tuples.
# ---------------------------------------------------------------------------

_PriceEntry = Union[Tuple[float, float], Tuple[float, float, float, float]]

_DEFAULT_PRICING: dict[str, _PriceEntry] = {
    # ── Anthropic Claude (input, output, cache_read, cache_write) ─────────────
    # claude-opus-4 / claude-3-opus-*
    "claude-opus-4":              (15.00, 75.00, 1.50, 18.75),
    "claude-opus-4-5":            (15.00, 75.00, 1.50, 18.75),
    "claude-3-opus":              (15.00, 75.00, 1.50, 18.75),
    # claude-sonnet-4-6 / claude-3-5-sonnet-*
    "claude-sonnet-4-6":          (3.00, 15.00, 0.30, 3.75),
    "claude-sonnet-4-5":          (3.00, 15.00, 0.30, 3.75),
    "claude-3-5-sonnet":          (3.00, 15.00, 0.30, 3.75),
    # claude-3-haiku (legacy)
    "claude-3-haiku":             (0.25,  1.25, 0.03, 0.30),
    # claude-haiku-4-5 / claude-3-5-haiku-*
    "claude-haiku-4-5":           (0.80,  4.00, 0.08, 1.00),
    "claude-haiku-3-5":           (0.80,  4.00, 0.08, 1.00),
    "claude-3-5-haiku":           (0.80,  4.00, 0.08, 1.00),

    # ── Google Gemini (input, output) ─────────────────────────────────────────
    "gemini-2.5-pro":             (1.25, 10.00),
    "gemini-2.5-flash":           (0.075, 0.30),
    "gemini-2.0-flash":           (0.075, 0.30),

    # ── OpenAI ───────────────────────────────────────────────────────────────
    "gpt-4o":                     (2.50, 10.00),
    "gpt-4o-mini":                (0.15,  0.60),
}

# Sentinel value returned when model is not in the pricing table.
# Distinguishable from zero (free/unknown) vs known cost.
UNKNOWN_COST: float = -1.0


def _load_pricing() -> dict[str, _PriceEntry]:
    """Return the merged pricing table (defaults + env override)."""
    table: dict[str, _PriceEntry] = dict(_DEFAULT_PRICING)
    override_raw = os.getenv("AGENTWEAVE_PRICING_OVERRIDE", "").strip()
    if override_raw:
        try:
            overrides = json.loads(override_raw)
            for model, prices in overrides.items():
                if isinstance(prices, (list, tuple)) and len(prices) in (2, 4):
                    table[model.lower()] = tuple(float(p) for p in prices)  # type: ignore[assignment]
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
    table: dict[str, _PriceEntry],
) -> Optional[_PriceEntry]:
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
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Compute USD cost for a single LLM call.

    For Anthropic models with prompt-caching, pass the token breakdown
    separately so each bucket is priced at the correct rate.

    Args:
        model: Model name (provider prefix is stripped automatically).
        input_tokens: Total prompt tokens (including cache tokens when the
            caller doesn't separate them).  When *cache_read_tokens* or
            *cache_write_tokens* are provided, ``input_tokens`` is treated as
            the **total** and the cache buckets are subtracted to get the
            uncached portion (floored at 0).
        output_tokens: Number of output/completion tokens generated.
        cache_read_tokens: Tokens read from the prompt cache (cheaper rate).
        cache_write_tokens: Tokens written to the prompt cache (slightly higher
            than regular input for Anthropic).

    Returns:
        Cost in USD as a float, or ``UNKNOWN_COST`` (-1.0) if the model
        is not found in the pricing table.

    Examples::
        compute_cost("claude-sonnet-4-6", 1_000_000, 0)     → 3.00
        compute_cost("anthropic/gpt-4o", 1_000_000, 0)      → 2.50
        compute_cost("unknown-model", 100, 50)               → -1.0

        # Cache-aware call (Anthropic):
        compute_cost(
            "claude-sonnet-4-6",
            input_tokens=1_100_000,
            output_tokens=0,
            cache_read_tokens=1_000_000,
            cache_write_tokens=50_000,
        )
    """
    table = _load_pricing()
    prices = _find_model_pricing(model, table)
    if prices is None:
        return UNKNOWN_COST

    input_price = prices[0]
    output_price = prices[1]

    if len(prices) == 4 and (cache_read_tokens > 0 or cache_write_tokens > 0):
        # Cache-aware pricing path (Anthropic models)
        cache_read_price: float = prices[2]   # type: ignore[index]
        cache_write_price: float = prices[3]  # type: ignore[index]

        # Uncached input = total prompt tokens minus the cached buckets (≥ 0)
        uncached_input = max(0, input_tokens - cache_read_tokens - cache_write_tokens)

        cost = (
            (cache_read_tokens  * cache_read_price  / 1_000_000)
            + (cache_write_tokens * cache_write_price / 1_000_000)
            + (uncached_input     * input_price       / 1_000_000)
            + (output_tokens      * output_price      / 1_000_000)
        )
    else:
        # Simple (non-cache) pricing path
        cost = (
            (input_tokens  * input_price  / 1_000_000)
            + (output_tokens * output_price / 1_000_000)
        )

    return cost
