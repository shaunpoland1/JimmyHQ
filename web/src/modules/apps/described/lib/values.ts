// Showing and reading values at the display edge. Everything returned for display is a plain
// string, to be rendered as text. A link is only ever a record's id plus a label the server
// gave: there is nothing here that makes an address from a value.

import type { FieldSpec, LinkLabels, TotalKind, Value } from "../api.ts";
import { MAX_CELL, cleanText } from "./schema.ts";

export type DisplayContext = {
  labels: LinkLabels;
  /** The browser's locale unless one is given. */
  locale?: string;
};

export const MISSING_RECORD = "Missing record";

/** How many digits a currency keeps after its point (2 for USD, 0 for JPY, 3 for BHD). */
export function minorDigits(currency: string, locale?: string): number {
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** Whole minor units as an amount: 1250 USD becomes "$12.50". */
export function formatMoney(minor: number, currency: string, locale?: string): string {
  const digits = minorDigits(currency, locale);
  try {
    return new Intl.NumberFormat(locale, { style: "currency", currency }).format(minor / 10 ** digits);
  } catch {
    return String(minor);
  }
}

/** A typed amount ("12.50", "12,5", "12") as whole minor units, or null if it is not one. */
export function parseMoney(text: string, currency: string, locale?: string): number | null {
  const digits = minorDigits(currency, locale);
  const match = /^(-?)(\d{1,15})(?:[.,](\d*))?$/.exec(text.trim());
  if (!match) return null;
  const fraction = match[3] ?? "";
  if (fraction.length > digits) return null;
  const minor = Number(`${match[2]}${fraction.padEnd(digits, "0")}`);
  if (!Number.isSafeInteger(minor)) return null;
  return match[1] ? -minor : minor;
}

/** The text a money field's form shows for its stored value: "12.50". */
export function moneyInput(minor: number, currency: string, locale?: string): string {
  const digits = minorDigits(currency, locale);
  const text = (Math.abs(minor) / 10 ** digits).toFixed(digits);
  return minor < 0 ? `-${text}` : text;
}

function numberText(value: number, locale?: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 6 }).format(value);
}

/** A "YYYY-MM-DD" date as "Nov 18, 2030". A date is a calendar day, so no time zone applies. */
export function formatDate(day: string, locale?: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!match) return cleanText(day, 20);
  const ms = Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }).format(new Date(ms));
}

/** A timestamp (unix milliseconds, UTC) as a date and time in the owner's time zone. */
export function formatStamp(ts: number, timeZone: string, locale?: string): string {
  const zone = timeZone || undefined;
  try {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: zone }).format(new Date(ts));
  } catch {
    return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(new Date(ts));
  }
}

/** The text a value shows as. Empty is "". Never markup. */
export function displayValue(spec: FieldSpec, field: string, value: Value | undefined, ctx: DisplayContext): string {
  if (value === null || value === undefined) return "";
  switch (spec.type) {
    case "bool":
      return value === true ? "Yes" : value === false ? "No" : "";
    case "int":
    case "number":
    case "computed":
      return typeof value === "number" ? numberText(value, ctx.locale) : "";
    case "money":
      return typeof value === "number" ? formatMoney(value, spec.currency ?? "USD", ctx.locale) : "";
    case "date":
      return typeof value === "string" ? formatDate(value, ctx.locale) : "";
    case "link": {
      const label = ctx.labels[field]?.[String(value)];
      return label === undefined ? MISSING_RECORD : cleanText(label);
    }
    default:
      return cleanText(typeof value === "string" ? value : String(value), MAX_CELL);
  }
}

/** The text of a total: a count is a count, a sum of money is money. */
export function displayTotal(kind: TotalKind, spec: FieldSpec, value: Value | undefined, locale?: string): string {
  if (value === null || value === undefined) return "";
  if (kind === "count") return typeof value === "number" ? numberText(value, locale) : "";
  if (spec.type === "date") return typeof value === "string" ? formatDate(value, locale) : "";
  if (typeof value !== "number") return "";
  return spec.type === "money" ? formatMoney(value, spec.currency ?? "USD", locale) : numberText(value, locale);
}

const TOTAL_WORDS: Record<TotalKind, string> = { sum: "Total", avg: "Average", min: "Lowest", max: "Highest", count: "Filled" };

export function totalWord(kind: TotalKind, spec?: FieldSpec): string {
  if (spec?.type === "date" && kind === "min") return "Earliest";
  if (spec?.type === "date" && kind === "max") return "Latest";
  return TOTAL_WORDS[kind];
}
