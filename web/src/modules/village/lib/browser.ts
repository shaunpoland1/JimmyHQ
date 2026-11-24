// The Browser tab's words and sums: what the owner is told about an agent's browser.
// Nothing here touches the network or the page.

import type { BrowserRefusal, BrowserState, BrowserUse, SubmitLevel } from "../api.ts";

export const LEVELS: readonly SubmitLevel[] = ["read", "approve", "alone"];
export const LEVEL_LABELS: Record<SubmitLevel, string> = {
  read: "Read only",
  approve: "Ask me each time",
  alone: "Alone",
};
/** What each level means for a form that changes something, in the owner's words. */
export const LEVEL_HELP: Record<SubmitLevel, string> = {
  read: "A form that would change something is never sent. The agent is told to ask you.",
  approve: "The agent files a card on your Bench showing exactly what would be sent. It goes only when you approve, and only if the page has not changed since.",
  alone: "The form is sent without asking, and a card tells you what was sent. \"No\" on that card puts the agent back to asking each time.",
};

/** How often the tab reads again: often while a page is open, seldom otherwise. */
export const WATCH_MS = 4_000;
export const IDLE_MS = 20_000;
export function refreshEvery(state: Pick<BrowserState, "running"> | undefined): number {
  return state?.running ? WATCH_MS : IDLE_MS;
}

/** One sentence for where the browser stands right now. */
export function standing(state: Pick<BrowserState, "held" | "ready" | "running" | "tabs">, name: string): string {
  if (!state.held) return `${name} has no browser. Give it "A browser of its own" under Overview, How it is set up.`;
  if (!state.ready) return `${name} may use a browser, but none is installed on this machine yet.`;
  if (!state.running) return `${name}'s browser is closed. It opens when ${name} next opens a page, and closes again when unused.`;
  const count = state.tabs.length;
  return `${name}'s browser is open with ${count} ${count === 1 ? "tab" : "tabs"}.`;
}

/** "12 of 100 today", and whether the day's allowance is used up. */
export function useLine(use: BrowserUse | undefined): { text: string; spent: boolean } | null {
  if (!use || typeof use.used !== "number" || typeof use.cap !== "number") return null;
  return { text: `${use.used} of ${use.cap} today`, spent: use.used >= use.cap };
}

/** The picture's address. The time makes each new picture a new address, so it is fetched again. */
export function pictureUrl(agentId: string, pictureMs: number | null | undefined): string | null {
  if (!pictureMs) return null;
  return `/api/village/agents/${encodeURIComponent(agentId)}/browser/picture?t=${pictureMs}`;
}

/** The tab in front, or the first one. */
export function frontTab<T extends { active: boolean }>(tabs: readonly T[]): T | null {
  return tabs.find((tab) => tab.active) ?? tabs[0] ?? null;
}

/** An allow-list as typed, one site to a line or separated by commas, tidied the way the server keeps it. */
export function parseHosts(text: string): string[] {
  const out: string[] = [];
  for (const piece of text.split(/[\n,]+/)) {
    const host = piece.trim().toLowerCase().replace(/^\.+|\.+$/g, "");
    if (host && !out.includes(host)) out.push(host);
  }
  return out;
}

/** The first entry that is not a site's name on its own, or null when every one is. */
export function badHost(hosts: readonly string[]): string | null {
  return hosts.find((host) => host.includes("://") || /[/@:\s]/.test(host) || host === "*" || host === "*.*") ?? null;
}

export function sameHosts(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((host, at) => host === b[at]);
}

const RULE_WORDS: Record<string, string> = {
  browser_hard_deny: "on your deny list",
  browser_allow_list: "not on this agent's allow-list",
  browser_public_address: "on this machine or a private network",
  browser_address_shape: "not a web address",
};

/** Why the fence refused a request, in a few words. */
export function refusalWords(refusal: Pick<BrowserRefusal, "host" | "rule">): string {
  return `${refusal.host || "an address"}: ${RULE_WORDS[refusal.rule] ?? "refused"}`;
}
