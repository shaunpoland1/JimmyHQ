// Chat and web lookup: reading the step events a reply streams, the activity
// line they make, the sources a finished reply keeps, and the Web switch.
// No browser or React in here.

import { formatBytes } from "../../../lib/format.ts";
import { hostOf, safeWebUrl, shortAddress } from "../../lookup/lib/url.ts";
import type { LookupStatus } from "../../lookup/api.ts";
import type { ChatOptions, Message, WebStepEvent, WebUsed } from "../api.ts";

export const WEB_LABEL = "Web";

// ---- the step events ----

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function wholeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/** One `step` event's data as the screen understands it, or null for anything unknown or unreadable. */
export function readStepEvent(value: unknown): WebStepEvent | null {
  const data = record(value);
  if (!data) return null;
  const id = wholeNumber(data.id);
  switch (data.type) {
    case "searching":
      return id !== null && typeof data.query === "string" ? { type: "searching", id, query: data.query } : null;
    case "results": {
      if (id === null || !Array.isArray(data.results)) return null;
      const results: { title: string; url: string }[] = [];
      for (const item of data.results) {
        const result = record(item);
        if (result && typeof result.url === "string") results.push({ title: typeof result.title === "string" ? result.title : "", url: result.url });
      }
      return { type: "results", id, results };
    }
    case "reading":
      return id !== null && typeof data.url === "string" ? { type: "reading", id, url: data.url } : null;
    case "read":
      return id !== null && typeof data.url === "string"
        ? {
            type: "read",
            id,
            url: data.url,
            title: typeof data.title === "string" ? data.title : "",
            bytes: typeof data.bytes === "number" && data.bytes >= 0 ? data.bytes : 0,
            truncated: data.truncated === true,
          }
        : null;
    case "failed":
      return typeof data.detail === "string" && data.detail ? { type: "failed", id, detail: data.detail } : null;
    default:
      return null;
  }
}

export type StepState = "open" | "done" | "failed" | "stopped";

/** One line of the activity: a search, a page read, or something that went wrong on its own. */
export type StepRow =
  | { kind: "search"; id: number; state: StepState; query: string; results: { title: string; url: string }[]; detail: string | null }
  | { kind: "page"; id: number; state: StepState; url: string; title: string; bytes: number | null; truncated: boolean; detail: string | null }
  | { kind: "problem"; id: number | null; state: "failed"; detail: string };

/** The most lines kept: one reply cannot hold more than this many lookups in a sane run. */
const MAX_ROWS = 60;

function capped(rows: StepRow[]): StepRow[] {
  return rows.length > MAX_ROWS ? rows.slice(rows.length - MAX_ROWS) : rows;
}

function indexOfOpen(rows: readonly StepRow[], id: number, kind: "search" | "page"): number {
  return rows.findIndex((row) => row.kind === kind && row.id === id);
}

/**
 * Lays one step event over the lines so far. A step is opened by `searching`
 * or `reading` and closed by an event with the same id; a `failed` that
 * closes nothing (refused before it began, or about the whole reply) is a
 * line of its own.
 */
export function applyStep(rows: readonly StepRow[], event: WebStepEvent): StepRow[] {
  const next = [...rows];
  switch (event.type) {
    case "searching": {
      const row: StepRow = { kind: "search", id: event.id, state: "open", query: event.query, results: [], detail: null };
      const at = indexOfOpen(next, event.id, "search");
      if (at >= 0) next[at] = row;
      else next.push(row);
      break;
    }
    case "results": {
      const at = indexOfOpen(next, event.id, "search");
      const was = at >= 0 ? next[at] : undefined;
      const row: StepRow = { kind: "search", id: event.id, state: "done", query: was?.kind === "search" ? was.query : "", results: event.results, detail: null };
      if (at >= 0) next[at] = row;
      else next.push(row);
      break;
    }
    case "reading": {
      const row: StepRow = { kind: "page", id: event.id, state: "open", url: event.url, title: "", bytes: null, truncated: false, detail: null };
      const at = indexOfOpen(next, event.id, "page");
      if (at >= 0) next[at] = row;
      else next.push(row);
      break;
    }
    case "read": {
      const row: StepRow = { kind: "page", id: event.id, state: "done", url: event.url, title: event.title, bytes: event.bytes, truncated: event.truncated, detail: null };
      const at = indexOfOpen(next, event.id, "page");
      if (at >= 0) next[at] = row;
      else next.push(row);
      break;
    }
    case "failed": {
      const at = event.id === null ? -1 : next.findIndex((row) => row.kind !== "problem" && row.id === event.id && row.state === "open");
      const was = at >= 0 ? next[at] : undefined;
      if (was && was.kind !== "problem") next[at] = { ...was, state: "failed", detail: event.detail };
      else next.push({ kind: "problem", id: event.id, state: "failed", detail: event.detail });
      break;
    }
  }
  return capped(next);
}

/** The same lines with every open one marked stopped: what is shown once the reply is being stopped. */
export function settleOpen(rows: readonly StepRow[]): StepRow[] {
  return rows.map((row) => (row.kind !== "problem" && row.state === "open" ? { ...row, state: "stopped" as const } : row));
}

export function isWorking(rows: readonly StepRow[]): boolean {
  return rows.some((row) => row.kind !== "problem" && row.state === "open");
}

function clip(text: string, max: number): string {
  const one = text.replace(/\s+/g, " ").trim();
  return one.length <= max ? one : `${one.slice(0, Math.max(1, max - 1))}…`;
}

function plural(count: number, one: string, many: string): string {
  return count === 1 ? `1 ${one}` : `${new Intl.NumberFormat().format(count)} ${many}`;
}

/** What one line says, in plain words. */
export function rowLabel(row: StepRow): string {
  if (row.kind === "problem") return row.detail;
  if (row.kind === "search") {
    const asked = row.query ? ` for “${clip(row.query, 80)}”` : "";
    switch (row.state) {
      case "open":
        return `Searching${asked}…`;
      case "stopped":
        return `Stopped while searching${asked}`;
      case "failed":
        return row.detail ?? `The search${asked} did not work.`;
      default:
        return row.results.length === 0 ? `No results${asked}` : `${plural(row.results.length, "result", "results")}${asked}`;
    }
  }
  const where = shortAddress(row.url, 56);
  switch (row.state) {
    case "open":
      return `Reading ${where}…`;
    case "stopped":
      return `Stopped while reading ${where}`;
    case "failed":
      return row.detail ?? `${where} could not be read.`;
    default: {
      const name = row.title.trim() ? clip(row.title, 80) : where;
      const facts = [row.bytes !== null && row.bytes > 0 ? formatBytes(row.bytes) : null, row.truncated ? "cut short" : null].filter(Boolean).join(", ");
      return facts ? `Read: ${name} (${facts})` : `Read: ${name}`;
    }
  }
}

/**
 * The one line the activity collapses to. While something is being done it
 * says what; afterwards it counts what was done. Null when there is nothing.
 */
export function summaryLine(rows: readonly StepRow[]): string | null {
  if (rows.length === 0) return null;
  const open = [...rows].reverse().find((row) => row.kind !== "problem" && row.state === "open");
  if (open) return rowLabel(open);
  const searches = rows.filter((row) => row.kind === "search" && row.state === "done").length;
  const pages = rows.filter((row) => row.kind === "page" && row.state === "done").length;
  const failed = rows.filter((row) => row.state === "failed").length;
  const stopped = rows.some((row) => row.state === "stopped");
  const parts = [
    searches > 0 ? `searched ${searches === 1 ? "once" : `${searches} times`}` : null,
    pages > 0 ? `read ${plural(pages, "page", "pages")}` : null,
    failed > 0 ? `${failed === 1 ? "1 lookup" : `${failed} lookups`} did not work` : null,
    stopped ? "stopped" : null,
  ].filter((part): part is string => part !== null);
  if (parts.length === 0) return "Web lookup";
  const text = parts.join(", ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A row's results as lines: a title always, the host and a safe link when the address is an ordinary web one. */
export type ResultLine = { title: string; host: string; href: string | null };

export function resultLines(results: readonly { title: string; url: string }[]): ResultLine[] {
  return results.map((item) => {
    const host = hostOf(item.url);
    return { title: item.title.trim() ? clip(item.title, 120) : host || item.url, host, href: safeWebUrl(item.url) };
  });
}

// ---- what a finished reply keeps ----

export type SourceLine = { title: string; host: string; href: string | null; read: boolean };

export type WebNote =
  | { kind: "used"; label: string; detail: string | null; read: SourceLine[]; seen: SourceLine[] }
  | { kind: "skipped"; reason: string };

function sourceLine(source: { title: string; url: string; read: boolean }): SourceLine {
  const host = hostOf(source.url);
  return { title: source.title.trim() ? clip(source.title, 160) : host || source.url, host, href: safeWebUrl(source.url), read: source.read === true };
}

/** What to show under a reply about the web, or null when the web was not asked for. */
export function webNote(web: WebUsed | null | undefined): WebNote | null {
  if (!web) return null;
  const lines = (Array.isArray(web.sources) ? web.sources : []).filter((source) => source && typeof source.url === "string").map(sourceLine);
  if (!web.used || lines.length === 0) {
    const failed = web.failed > 0 ? `${plural(web.failed, "lookup", "lookups")} did not work, so nothing from the web was used.` : null;
    return { kind: "skipped", reason: web.reason ?? failed ?? "Web was on, and the model did not look anything up." };
  }
  const read = lines.filter((line) => line.read);
  const seen = lines.filter((line) => !line.read);
  const label = read.length > 0 ? `Used ${plural(read.length, "source", "sources")}` : `Looked at ${plural(seen.length, "search result", "search results")}`;
  const parts = [
    web.searches > 0 ? `${plural(web.searches, "search", "searches")}` : null,
    web.pages > 0 ? `${plural(web.pages, "page", "pages")} read` : null,
    web.failed > 0 ? `${plural(web.failed, "lookup", "lookups")} did not work` : null,
  ].filter((part): part is string => part !== null);
  return { kind: "used", label, detail: parts.length > 0 ? parts.join(", ") : null, read, seen };
}

// ---- the Web switch ----

/**
 * The switch as it stands in a conversation that has replies: what the last
 * reply was written with. null when there is no reply to learn it from.
 */
export function inferWeb(messages: readonly Message[]): boolean | null {
  for (let at = messages.length - 1; at >= 0; at -= 1) {
    const message = messages[at];
    if (message?.role === "assistant") return message.web != null;
  }
  return null;
}

export type WebToggle =
  | { kind: "loading"; disabled: true; reason: null }
  | { kind: "ready"; disabled: false; reason: null }
  | { kind: "off"; disabled: true; reason: string }
  | { kind: "unready"; disabled: false; reason: string };

/**
 * Whether the switch can be used: web lookup must be on. With no search
 * provider ready it still works, for reading a page whose address the owner
 * gives, and says so. The lookup status is fresher than the composer's options when both
 * are known; the options stand in until it arrives (or if it cannot be read).
 */
export function webToggle(web: ChatOptions["web"] | undefined, status: Pick<LookupStatus, "enabled" | "search_ready" | "search_problem"> | undefined): WebToggle {
  if (!web && !status) return { kind: "loading", disabled: true, reason: null };
  const enabled = status?.enabled ?? web?.enabled ?? false;
  if (!enabled) return { kind: "off", disabled: true, reason: "Web lookup is off." };
  const ready = status ? status.search_ready : (web?.search ?? false);
  if (!ready) return { kind: "unready", disabled: false, reason: status?.search_problem ?? "No search provider is set up yet." };
  return { kind: "ready", disabled: false, reason: null };
}

/** Where the Web switch starts out in a conversation with no reply to learn it from. */
export function webStart(web: ChatOptions["web"] | undefined, status: Pick<LookupStatus, "enabled" | "chat_default"> | undefined): boolean {
  if (status) return status.enabled && status.chat_default;
  return web?.default ?? false;
}

/** Where the searches go, as a phrase. */
export function searchTarget(provider: string | null | undefined, label: string | null | undefined): string {
  if (provider === "searxng") return "your own SearXNG server";
  if (provider && provider !== "none" && label) return label;
  return "the search provider you chose";
}

/** The one line that says what "Web" means as it stands, and what leaves the machine. */
export function webHelp(on: boolean, context: { searchTarget: string; localOnly: boolean }): string {
  if (!on) return "Web is off: the model will not search the web or read pages for your next message.";
  const pages = context.localOnly
    ? "This conversation is local only, so what the pages say goes only to your own models."
    : "If the model answering is hosted, what the pages say is sent to its provider.";
  return `Web is on: the model may search the web and read pages for your next message. Its search words go to ${context.searchTarget}. Pages are fetched from this computer. ${pages}`;
}

// ---- addresses in a reply ----

function trimmedAddress(found: string): { address: string; rest: string } {
  let address = found;
  let rest = "";
  for (;;) {
    const last = address.charAt(address.length - 1);
    const open = (address.match(/\(/g) ?? []).length;
    const close = (address.match(/\)/g) ?? []).length;
    if (/[.,;:!?'"*_~\]]/.test(last) || (last === ")" && close > open)) {
      rest = last + rest;
      address = address.slice(0, -1);
    } else break;
  }
  return { address, rest };
}

function linkLine(text: string): string {
  return text.replace(/(?<![\w<["'=])(?<!\]\()https?:\/\/[^\s<>]+/g, (found) => {
    const { address, rest } = trimmedAddress(found);
    const safe = safeWebUrl(address);
    return safe ? `<${address}>${rest}` : found;
  });
}

/**
 * Makes a plain web address in a model's words a link, by wrapping it in
 * angle brackets (Markdown's own form of a link), leaving code and addresses
 * that are already links alone. Only http and https addresses are touched.
 */
export function linkBareAddresses(text: string): string {
  if (!text.includes("http")) return text;
  let fenced = false;
  return text
    .split("\n")
    .map((line) => {
      if (/^\s{0,3}(```|~~~)/.test(line)) {
        fenced = !fenced;
        return line;
      }
      if (fenced) return line;
      // Odd parts of the split are inline code.
      return line
        .split(/(`+[^`]*`+)/)
        .map((part, at) => (at % 2 === 1 ? part : linkLine(part)))
        .join("");
    })
    .join("\n");
}
