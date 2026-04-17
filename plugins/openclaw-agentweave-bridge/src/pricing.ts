/**
 * LLM pricing fallback for the openclaw-agentweave-bridge plugin.
 *
 * OpenClaw emits a `model.usage` event with `costUsd`; for models it doesn't
 * know, `costUsd` is 0. This module fills that gap so spans written to Tempo
 * carry a real `cost.usd` value.
 *
 * Entries MUST stay in sync with `sdk/python/agentweave/pricing.py`. Prices
 * are USD per 1 million tokens.
 */

type PriceEntry =
  | readonly [input: number, output: number]
  | readonly [input: number, output: number, cacheRead: number, cacheWrite: number]

const PRICING: Record<string, PriceEntry> = {
  // ── Anthropic ──────────────────────────────────────────────────────────────
  "claude-opus-4":              [15.00, 75.00, 1.50, 18.75],
  "claude-opus-4-5":            [15.00, 75.00, 1.50, 18.75],
  "claude-3-opus":              [15.00, 75.00, 1.50, 18.75],
  "claude-sonnet-4-6":          [ 3.00, 15.00, 0.30,  3.75],
  "claude-sonnet-4-5":          [ 3.00, 15.00, 0.30,  3.75],
  "claude-3-5-sonnet":          [ 3.00, 15.00, 0.30,  3.75],
  "claude-3-haiku":             [ 0.25,  1.25, 0.03,  0.30],
  "claude-haiku-4-5":           [ 0.80,  4.00, 0.08,  1.00],
  "claude-haiku-3-5":           [ 0.80,  4.00, 0.08,  1.00],
  "claude-3-5-haiku":           [ 0.80,  4.00, 0.08,  1.00],
  // ── OpenAI / Codex aliases ────────────────────────────────────────────────
  "gpt-4o":                     [ 2.50, 10.00],
  "gpt-4o-mini":                [ 0.15,  0.60],
  "gpt-5.3":                    [ 2.50, 10.00],
  "gpt-5.3-codex":              [ 2.50, 10.00],
  "gpt-5.4":                    [ 2.50, 10.00],
  // ── MiniMax (official pay-as-you-go, highspeed tier) ──────────────────────
  "minimax-m2.7-highspeed":     [ 0.60,  2.40, 0.06, 0.375],
  "minimax-m2.5-highspeed":     [ 0.60,  2.40, 0.03, 0.375],
}

function normalize(model: string): string {
  const m = model.toLowerCase().trim()
  return m.includes("/") ? m.slice(m.indexOf("/") + 1) : m
}

function findPricing(model: string): PriceEntry | undefined {
  const n = normalize(model)
  if (n in PRICING) return PRICING[n]
  for (const [key, prices] of Object.entries(PRICING)) {
    if (key.includes(n) || n.includes(key)) return prices
  }
  return undefined
}

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  cacheReadTokens?: number
  cacheWriteTokens?: number
}

/**
 * Compute USD cost from tokens. Returns `undefined` when the model is not in
 * the table, so callers can distinguish "unknown" from "free".
 */
export function computeCost(model: string, u: TokenUsage): number | undefined {
  const prices = findPricing(model)
  if (!prices) return undefined

  const [inputPrice, outputPrice] = prices
  const cacheReadPrice = prices.length === 4 ? prices[2] : 0
  const cacheWritePrice = prices.length === 4 ? prices[3] : 0

  const cacheRead = u.cacheReadTokens ?? 0
  const cacheWrite = u.cacheWriteTokens ?? 0
  const uncachedInput = Math.max(0, u.inputTokens - cacheRead - cacheWrite)

  return (
    (uncachedInput * inputPrice) / 1_000_000 +
    (u.outputTokens * outputPrice) / 1_000_000 +
    (cacheRead * cacheReadPrice) / 1_000_000 +
    (cacheWrite * cacheWritePrice) / 1_000_000
  )
}

/**
 * If `upstreamCost` looks unreliable (0, negative, or NaN), fall back to the
 * local table. Callers who DO know the cost pass a positive value and get it
 * back unchanged.
 */
export function resolveCost(
  upstreamCost: number,
  model: string,
  usage: TokenUsage,
): number {
  if (Number.isFinite(upstreamCost) && upstreamCost > 0) return upstreamCost
  const fallback = computeCost(model, usage)
  return fallback ?? 0
}
