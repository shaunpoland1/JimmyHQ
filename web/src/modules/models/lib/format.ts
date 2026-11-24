// Number and money formatting for model usage.

import type { UsageResponse, UsageWindow } from "../api.ts";

function oneDecimal(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(value);
}

/** Compact count: 950 → "950", 46 512 → "46.5K", 182 800 000 → "182.8M". */
export function formatCompact(value: number, locale?: string): string {
  if (!Number.isFinite(value) || value <= 0) return "0";
  const steps: readonly [number, string][] = [
    [1e3, "K"],
    [1e6, "M"],
    [1e9, "B"],
    [1e12, "T"],
  ];
  if (value < 1e3) return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value);
  let scaled = 0;
  let suffix = "";
  for (const [size, unit] of steps) {
    scaled = Math.round(value / (size / 10)) / 10;
    suffix = unit;
    // Move up a unit only when rounding would print "1000.0" of this one.
    if (scaled < 1000) break;
  }
  if (scaled >= 1000) return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(scaled)}${suffix}`;
  return `${oneDecimal(scaled, locale)}${suffix}`;
}

/**
 * Money with two decimals. `currency` is the owner's label: a three-letter
 * code is shown the way the locale writes it, anything else as a prefix.
 */
export function formatCost(amount: number, currency: string, locale?: string): string {
  const code = currency.trim();
  if (/^[A-Za-z]{3}$/.test(code)) {
    try {
      return new Intl.NumberFormat(locale, {
        style: "currency",
        currency: code.toUpperCase(),
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(amount);
    } catch {
      // Not a currency this browser knows: fall through to the plain form.
    }
  }
  const number = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount);
  return code ? `${code} ${number}` : number;
}

/** A cost for a table cell: tiny amounts are not rounded down to nothing. */
export function formatCostCell(amount: number, currency: string, locale?: string): string {
  if (amount > 0 && amount < 0.005) return `< ${formatCost(0.01, currency, locale)}`;
  return formatCost(amount, currency, locale);
}

type UsageTotals = Pick<UsageWindow, "calls" | "input_tokens" | "output_tokens" | "estimated_cost" | "saved_by_local">;

/**
 * The model part of the top strip's usage line, e.g.
 * "LLM 46.5K calls · 182.8M tok · est $243.49 · saved $201.12 local".
 * Parts that are null or zero are left out; no calls at all gives null.
 */
export function formatModelUsage(totals: UsageTotals, currency: string, locale?: string): string | null {
  if (!(totals.calls > 0)) return null;
  const parts = [`LLM ${formatCompact(totals.calls, locale)} ${totals.calls === 1 ? "call" : "calls"}`];
  const tokens = totals.input_tokens + totals.output_tokens;
  if (tokens > 0) parts.push(`${formatCompact(tokens, locale)} tok`);
  if (totals.estimated_cost >= 0.005) parts.push(`est ${formatCost(totals.estimated_cost, currency, locale)}`);
  if (totals.saved_by_local !== null && totals.saved_by_local >= 0.005) {
    parts.push(`saved ${formatCost(totals.saved_by_local, currency, locale)} local`);
  }
  return parts.join(" · ");
}

export function usageText(usage: UsageResponse | undefined, locale?: string): string | null {
  return usage ? formatModelUsage(usage.all_time, usage.currency, locale) : null;
}

/** "1.2 s" or "340 ms". */
export function formatDuration(ms: number, locale?: string): string {
  if (!Number.isFinite(ms) || ms < 0) return "0 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${oneDecimal(ms / 1000, locale)} s`;
}

/** A short "checked at" time in the browser's locale and time zone. */
export function formatChecked(ms: number, locale?: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone }).format(new Date(ms));
}
