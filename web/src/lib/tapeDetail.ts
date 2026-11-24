// How one Tape event reads when a row is opened: a label/value list built from
// its payload, in plain words. Pure, so the wording is tested.

import type { TapeEvent } from "../api.ts";

export const HIDDEN = "hidden";
export const HIDDEN_WHY = "Hidden because its name looks like a password, key or token. It stays hidden in the raw view too.";
export const LONG_TEXT = 200;
const MAX_DEPTH = 4;
const MAX_ROWS = 80;
const MIN_MS = 946_684_800_000; // 2000-01-01
const MAX_MS = 4_102_444_800_000; // 2100-01-01

export type DetailRow = {
  label: string;
  /** How far in, for nested values. */
  depth: number;
  /** "group" rows only introduce the rows beneath them. */
  type: "text" | "id" | "time" | "hidden" | "group";
  /** What is shown. Ids are short; the full id is in `full`. */
  value: string;
  /** The whole value, for ids (copy) and long text (show more). */
  full: string;
};

const SECRET_NAME = /pass(?:word|code|phrase)|secret|token|api[_ -]?key|credential|authorization|cookie|private[_ -]?key/i;
const REMOVED = "[removed]";
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/;
const ID_VALUE = /^(?:[0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{16,})$/;
const ACRONYMS = new Set(["id", "ids", "url", "uri", "ai", "api", "ui", "ocr", "tts", "stt", "sms", "ip", "http", "json"]);

/** "chat.message.sent" becomes "Chat message sent". */
export function kindInWords(kind: string): string {
  const words = kind.split(/[._\-/\s]+/).filter(Boolean);
  if (words.length === 0) return kind;
  const text = words.join(" ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "sent_at" becomes "Sent at", "userId" becomes "User ID". */
export function fieldLabel(key: string): string {
  const spaced = key.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[._\-/\s]+/g, " ").trim();
  if (!spaced) return key;
  const words = spaced.split(" ").map((word, index) => {
    const lower = word.toLowerCase();
    if (ACRONYMS.has(lower)) return lower === "ids" ? "IDs" : lower.toUpperCase();
    return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
  });
  return words.join(" ");
}

export function isSecretName(key: string): boolean {
  return SECRET_NAME.test(key);
}

function isTimeName(key: string): boolean {
  return /(?:^|[_\s-])(?:ts|at|time|timestamp)$|[a-z](?:At|Time|Ts)$|_ms$/.test(key) || key === "ts" || key === "at";
}

function isIdName(key: string): boolean {
  return key === "id" || /(?:_id|Id|_ids)$/.test(key) || key === "cause" || key === "root";
}

/** Local date and time, e.g. "Nov 19, 2026, 3:04:05 PM". */
export function formatLocalTime(ms: number, locale?: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium", timeZone }).format(new Date(ms));
}

/** "just now", "5 minutes ago", "2 days ago", "in 3 minutes". */
export function formatRelative(ms: number, now: number = Date.now(), locale?: string): string {
  const diff = ms - now;
  const abs = Math.abs(diff);
  if (abs < 5_000) return "just now";
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
    ["second", 1_000],
  ];
  const [unit, size] = units.find(([, s]) => abs >= s) ?? units[units.length - 1]!;
  return new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(Math.round(diff / size), unit);
}

/** The first characters of an id, enough to tell ids apart. */
export function shortId(id: string): string {
  return id.length <= 10 ? id : `${id.slice(0, 8)}…`;
}

function isTimeValue(key: string, value: unknown): value is number | string {
  if (typeof value === "number") return isTimeName(key) && Number.isFinite(value) && value >= MIN_MS && value <= MAX_MS;
  return typeof value === "string" && ISO.test(value) && !Number.isNaN(Date.parse(value));
}

function scalar(key: string, value: unknown, locale?: string, timeZone?: string): Pick<DetailRow, "type" | "value" | "full"> {
  // A number under a name like "input_tokens" is a count, not a secret.
  if (isSecretName(key) && typeof value !== "number") return { type: "hidden", value: HIDDEN, full: HIDDEN };
  if (typeof value === "string" && value.includes(REMOVED)) return { type: "hidden", value: HIDDEN, full: HIDDEN };
  if (value === null || value === undefined) return { type: "text", value: "none", full: "none" };
  if (isTimeValue(key, value)) {
    const text = formatLocalTime(typeof value === "number" ? value : Date.parse(value), locale, timeZone);
    return { type: "time", value: text, full: text };
  }
  if (typeof value === "string") {
    if (value === "") return { type: "text", value: "empty", full: "empty" };
    if ((isIdName(key) || ID_VALUE.test(value)) && !/\s/.test(value) && value.length > 10) {
      return { type: "id", value: shortId(value), full: value };
    }
    return { type: "text", value, full: value };
  }
  if (typeof value === "boolean") return { type: "text", value: value ? "yes" : "no", full: value ? "yes" : "no" };
  const text = String(value);
  return { type: "text", value: text, full: text };
}

/** Turns a payload into rows, nested values indented. Never throws; secrets become "hidden". */
export function payloadRows(payload: unknown, locale?: string, timeZone?: string): DetailRow[] {
  const rows: DetailRow[] = [];
  const walk = (key: string, value: unknown, depth: number): void => {
    if (rows.length >= MAX_ROWS) return;
    if (value !== null && typeof value === "object" && !isSecretName(key)) {
      const entries: [string, unknown][] = Array.isArray(value)
        ? value.map((item, i) => [`Item ${i + 1}`, item])
        : Object.entries(value as Record<string, unknown>);
      if (entries.length === 0) {
        rows.push({ label: fieldLabel(key), depth, type: "text", value: "empty", full: "empty" });
        return;
      }
      if (depth >= MAX_DEPTH) {
        rows.push({ label: fieldLabel(key), depth, type: "text", value: `${entries.length} more, see Show raw`, full: "" });
        return;
      }
      rows.push({ label: fieldLabel(key), depth, type: "group", value: "", full: "" });
      for (const [k, v] of entries) walk(k, v, depth + 1);
      return;
    }
    rows.push({ label: fieldLabel(key), depth, ...scalar(key, value, locale, timeZone) });
  };
  if (payload !== null && typeof payload === "object" && !Array.isArray(payload)) {
    for (const [k, v] of Object.entries(payload as Record<string, unknown>)) walk(k, v, 0);
  }
  return rows;
}

/** The payload as JSON for "Show raw", with secrets taken out. */
export function redactedJson(payload: unknown): string {
  const clean = (key: string, value: unknown, depth: number): unknown => {
    if (isSecretName(key) && typeof value !== "number") return HIDDEN;
    if (typeof value === "string") return value.includes(REMOVED) ? HIDDEN : value;
    if (value === null || typeof value !== "object") return value;
    if (depth > 12) return "…";
    if (Array.isArray(value)) return value.map((item) => clean("", item, depth + 1));
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, clean(k, v, depth + 1)]));
  };
  return JSON.stringify(clean("", payload, 0), null, 2) ?? "{}";
}

/** A one-line hint for hovering a row. */
export function hoverHint(event: Pick<TapeEvent, "seq" | "ts" | "kind" | "actor" | "subject">, locale?: string, timeZone?: string): string {
  const parts = [`#${event.seq}`, formatLocalTime(event.ts, locale, timeZone), `${kindInWords(event.kind)} by ${event.actor}`];
  if (event.subject) parts.push(`about ${event.subject}`);
  return `${parts.join(" · ")}. Click for details.`;
}

export function isLongText(value: string): boolean {
  return value.length > LONG_TEXT;
}

export function capText(value: string): string {
  return isLongText(value) ? `${value.slice(0, LONG_TEXT).trimEnd()}…` : value;
}
