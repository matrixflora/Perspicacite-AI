"use client";

// Best-effort token-cost estimator. Prices are USD per million tokens
// and reflect public pricing for the providers we actually route to.
// When the model isn't in the table we return `null` so the UI can
// hide the cost line rather than make a number up.

type PriceRow = { input: number; output: number };

// Match keys against the model string (provider-prefixed form, e.g.
// "openrouter/deepseek/deepseek-chat"). Longest match wins.
const PRICES_PER_MILLION: Record<string, PriceRow> = {
  // DeepSeek (via OpenRouter or direct)
  "deepseek/deepseek-chat":     { input: 0.14, output: 0.28 },
  "deepseek/deepseek-reasoner": { input: 0.55, output: 2.19 },
  "deepseek-chat":              { input: 0.14, output: 0.28 },
  "deepseek-reasoner":          { input: 0.55, output: 2.19 },

  // OpenAI
  "gpt-4o-mini": { input: 0.15, output: 0.60 },
  "gpt-4o":      { input: 2.50, output: 10.00 },
  "gpt-4-turbo": { input: 10.0, output: 30.00 },
  "gpt-3.5-turbo": { input: 0.50, output: 1.50 },

  // Anthropic
  "claude-sonnet-4-5":  { input: 3.00, output: 15.00 },
  "claude-haiku-4-5":   { input: 1.00, output: 5.00 },
  "claude-opus-4-5":    { input: 15.0, output: 75.0 },
  "claude-3-5-sonnet":  { input: 3.00, output: 15.00 },
  "claude-3-5-haiku":   { input: 0.80, output: 4.00 },
};

export function estimateCostUsd(
  model: string | null | undefined,
  tokensIn: number,
  tokensOut: number,
): number | null {
  if (!model) return null;
  // Find the longest key that occurs in the model string.
  const m = model.toLowerCase();
  let best: { key: string; row: PriceRow } | null = null;
  for (const [key, row] of Object.entries(PRICES_PER_MILLION)) {
    if (m.includes(key) && (!best || key.length > best.key.length)) {
      best = { key, row };
    }
  }
  if (!best) return null;
  return (
    (tokensIn / 1_000_000) * best.row.input +
    (tokensOut / 1_000_000) * best.row.output
  );
}

// Format a USD amount sensibly: < $0.01 → "<$0.01", else 4 decimals
// up to $0.10, else 3, else 2.
export function formatUsd(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return "";
  if (cost < 0.0001) return "<$0.0001";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  if (cost < 0.10) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}
