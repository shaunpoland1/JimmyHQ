// Logic for the Web lookup settings card. No browser or React in here.

import type { LookupProvider, TryResult } from "../api.ts";
import { hostOf } from "./url.ts";

/** The longest key the server accepts. */
export const KEY_MAX_LENGTH = 500;

export type KeyCheck = { ok: true; key: string } | { ok: false; error: string };

/** The same rule the server applies: one run of printable characters, no spaces, at most 500. */
export function checkKey(raw: string): KeyCheck {
  const key = raw.trim();
  if (!key) return { ok: false, error: "Paste the key the search provider gave you." };
  if (key.length > KEY_MAX_LENGTH) return { ok: false, error: `A key can be at most ${KEY_MAX_LENGTH} characters.` };
  if (!/^[\x21-\x7e]+$/.test(key)) {
    return { ok: false, error: "Paste the key exactly as the provider gave it, with no spaces or line breaks." };
  }
  return { ok: true, key };
}

type FeedEvent = { kind: string; payload?: Record<string, unknown> };

/** True for a tape event after which the status may read differently. */
export function affectsLookupStatus(event: FeedEvent): boolean {
  if (event.kind.startsWith("lookup.")) return true;
  if (event.kind === "setting.updated" || event.kind === "setting.reset") {
    const key = event.payload?.key;
    return typeof key === "string" && key.startsWith("lookup.");
  }
  return false;
}

/** What each provider is, in a line or two. */
const ABOUT: Readonly<Record<string, string>> = {
  none: "No search. A model can still read a page whose address you give it.",
  exa: "A hosted search service built for programs. Needs a key from Exa.",
  tavily: "A hosted search service made for AI assistants. Needs a key from Tavily.",
  brave: "A hosted web search from Brave. Needs a key from Brave Search.",
  searxng: "A search server you run yourself. It needs no key, only its address, so searches stay in your own house.",
  jina: "A hosted search service from Jina. Needs a key from Jina.",
};

export function providerAbout(id: string): string {
  return ABOUT[id] ?? "";
}

/** The hint under the SearXNG address, which the generated setting row cannot hold. */
export const SEARXNG_HINT =
  "Set its address under 'SearXNG address' below, such as https://search.example.com. JSON output must be switched on in its settings.yml (search: formats: json). The address may be on your own network.";

export type KeyState = "saved" | "missing" | "none";

/** Whether a provider has a key saved, lacks one it needs, or takes none. */
export function keyState(provider: Pick<LookupProvider, "needs_key" | "key_set">): KeyState {
  if (!provider.needs_key) return "none";
  return provider.key_set ? "saved" : "missing";
}

export type Readiness = { tone: "ok" | "warn" | "off"; label: string };

/** The one word for the card's header. */
export function readiness(status: Pick<{ enabled: boolean; search_ready: boolean }, "enabled" | "search_ready">): Readiness {
  if (!status.enabled) return { tone: "off", label: "Off" };
  return status.search_ready ? { tone: "ok", label: "On and ready" } : { tone: "warn", label: "On, search not ready" };
}

/** Where searches go, in one sentence: what the card says about what leaves the machine. */
export function leavesLine(status: Pick<{ provider: string; provider_label: string }, "provider" | "provider_label">): string {
  if (status.provider === "none") return "With no search provider chosen, nothing is searched. A model can still read a page whose address you give it.";
  if (status.provider === "searxng") return "Search words go to your own SearXNG server. Pages are read from this computer. A hosted model that answers is sent what the pages say.";
  return `Search words go to ${status.provider_label}. Pages are read from this computer. A hosted model that answers is sent what the pages say.`;
}

/** "3 results in 0.4 s" for the search that was tried. */
export function tryLine(result: Pick<TryResult, "results" | "duration_ms" | "provider_label">, locale?: string): string {
  const count = result.results.length;
  const seconds = new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(Math.max(0, result.duration_ms) / 1000);
  const found = count === 0 ? "No results" : count === 1 ? "1 result" : `${new Intl.NumberFormat(locale).format(count)} results`;
  return `${found} from ${result.provider_label} in ${seconds} s`;
}

export type TryRow = { title: string; url: string; host: string; snippet: string };

/** The results of a tried search as rows: a title always, the host when the address is an ordinary web one. */
export function tryRows(result: Pick<TryResult, "results">): TryRow[] {
  return result.results.map((item) => ({
    title: item.title.trim() || hostOf(item.url) || item.url,
    url: item.url,
    host: hostOf(item.url),
    snippet: item.snippet.trim(),
  }));
}

/** The words sent to a search, as the server will take them, or null when there are none. */
export function searchWords(typed: string, max = 400): string | null {
  const words = typed.replace(/\s+/g, " ").trim();
  return words ? words.slice(0, max) : null;
}
