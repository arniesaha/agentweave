import { describe, it, expect } from "vitest"
import { computeCost, resolveCost } from "./pricing.js"

describe("computeCost", () => {
  it("returns undefined for unknown models", () => {
    expect(computeCost("never-heard-of-this-model", { inputTokens: 100, outputTokens: 50 })).toBeUndefined()
  })

  it("prices claude-sonnet-4-6 exactly ($3/M in, $15/M out)", () => {
    const cost = computeCost("claude-sonnet-4-6", { inputTokens: 1_000_000, outputTokens: 0 })
    expect(cost).toBeCloseTo(3.0, 9)
  })

  it("prices MiniMax-M2.7-highspeed per official rates", () => {
    // $0.60/M in, $2.40/M out
    const cost = computeCost("MiniMax-M2.7-highspeed", { inputTokens: 1_000_000, outputTokens: 1_000_000 })
    expect(cost).toBeCloseTo(3.0, 9)
  })

  it("handles cache-aware pricing for MiniMax-M2.7 ($0.06/M cache_read)", () => {
    const cost = computeCost("MiniMax-M2.7-highspeed", {
      inputTokens: 1_000_000,
      outputTokens: 0,
      cacheReadTokens: 1_000_000,
    })
    expect(cost).toBeCloseTo(0.06, 9)
  })

  it("M2.5 cache_read ($0.03/M) is half of M2.7 ($0.06/M)", () => {
    const m25 = computeCost("MiniMax-M2.5-highspeed", {
      inputTokens: 1_000_000, outputTokens: 0, cacheReadTokens: 1_000_000,
    })
    const m27 = computeCost("MiniMax-M2.7-highspeed", {
      inputTokens: 1_000_000, outputTokens: 0, cacheReadTokens: 1_000_000,
    })
    expect(m25).toBeCloseTo(0.03, 9)
    expect(m27).toBeCloseTo(0.06, 9)
  })

  it("case-insensitive + provider-prefix-stripping", () => {
    const a = computeCost("anthropic/claude-haiku-4-5", { inputTokens: 1_000_000, outputTokens: 0 })
    const b = computeCost("Claude-Haiku-4-5", { inputTokens: 1_000_000, outputTokens: 0 })
    expect(a).toBeCloseTo(0.80, 9)
    expect(b).toBeCloseTo(0.80, 9)
  })

  it("partial-matches versioned models (claude-sonnet-4-6-20250101 → claude-sonnet-4-6)", () => {
    const cost = computeCost("claude-sonnet-4-6-20250101", { inputTokens: 1_000_000, outputTokens: 0 })
    expect(cost).toBeCloseTo(3.0, 9)
  })
})

describe("resolveCost", () => {
  it("passes through a positive upstream cost unchanged", () => {
    const out = resolveCost(0.042, "MiniMax-M2.7-highspeed", { inputTokens: 100, outputTokens: 50 })
    expect(out).toBe(0.042)
  })

  it("falls back to local table when upstream is 0", () => {
    const out = resolveCost(0, "MiniMax-M2.7-highspeed", {
      inputTokens: 1_000_000, outputTokens: 1_000_000,
    })
    expect(out).toBeCloseTo(3.0, 9)
  })

  it("falls back when upstream is negative (sentinel) or NaN", () => {
    expect(resolveCost(-1, "claude-sonnet-4-6", { inputTokens: 1_000_000, outputTokens: 0 })).toBeCloseTo(3.0, 9)
    expect(resolveCost(NaN, "claude-sonnet-4-6", { inputTokens: 1_000_000, outputTokens: 0 })).toBeCloseTo(3.0, 9)
  })

  it("returns 0 when upstream is 0 AND model is unknown (no worse than status quo)", () => {
    const out = resolveCost(0, "exotic-model", { inputTokens: 1000, outputTokens: 500 })
    expect(out).toBe(0)
  })
})
