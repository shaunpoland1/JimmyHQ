// Connecting an account. The answer is "ready", "needs_key" or "redirect": for a redirect
// the owner finishes at the provider's page in another tab while the screen asks the server
// again and again (the finish call) until the account shows up, or the owner cancels, or
// too long has passed. This is the state machine for that; the screen only draws it.

import type { Account, ConnectAnswer } from "../api.ts";

/** How long to wait for the owner to finish at the provider's page. */
export const TIMEOUT_MS = 10 * 60 * 1000;
/** Failed checks in a row before the screen stops pretending it is still looking. */
export const MAX_FAILURES = 3;

export type ConnectState =
  | { phase: "form" }
  | { phase: "starting" }
  | { phase: "waiting"; url: string; startedAt: number; polls: number; failures: number; checking: boolean; lastError: string | null }
  | { phase: "needs_key" }
  | { phase: "ready"; account: Account | null }
  | { phase: "cancelled" }
  | { phase: "timeout" }
  | { phase: "failed"; error: string };

export type ConnectEvent =
  | { type: "start" }
  | { type: "answer"; answer: ConnectAnswer; now: number }
  | { type: "start_failed"; error: string }
  | { type: "checking" }
  | { type: "checked"; answer: ConnectAnswer }
  | { type: "check_failed"; error: string }
  | { type: "tick"; now: number }
  | { type: "cancel" }
  | { type: "again" };

export const START: ConnectState = { phase: "form" };

/** The provider's page may only be opened when it is an ordinary web address. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

export function connectReducer(state: ConnectState, event: ConnectEvent): ConnectState {
  switch (event.type) {
    case "start":
      return state.phase === "form" || state.phase === "failed" || state.phase === "timeout" || state.phase === "cancelled" || state.phase === "needs_key" ? { phase: "starting" } : state;
    case "start_failed":
      return state.phase === "starting" ? { phase: "failed", error: event.error } : state;
    case "answer": {
      if (state.phase !== "starting") return state;
      const { answer } = event;
      if (answer.status === "ready") return { phase: "ready", account: answer.account };
      if (answer.status === "needs_key") return { phase: "needs_key" };
      const url = safeUrl(answer.url);
      if (answer.status === "redirect" && url) return { phase: "waiting", url, startedAt: event.now, polls: 0, failures: 0, checking: false, lastError: null };
      return { phase: "failed", error: "The provider did not give a page to open. Try again, or check the provider under Settings, Integrations." };
    }
    case "checking":
      return state.phase === "waiting" ? { ...state, checking: true } : state;
    case "checked": {
      if (state.phase !== "waiting") return state;
      if (event.answer.status === "ready") return { phase: "ready", account: event.answer.account };
      return { ...state, checking: false, polls: state.polls + 1, failures: 0, lastError: null };
    }
    case "check_failed": {
      if (state.phase !== "waiting") return state;
      const failures = state.failures + 1;
      if (failures >= MAX_FAILURES) return { phase: "failed", error: event.error };
      return { ...state, checking: false, polls: state.polls + 1, failures, lastError: event.error };
    }
    case "tick":
      return state.phase === "waiting" && event.now - state.startedAt >= TIMEOUT_MS ? { phase: "timeout" } : state;
    case "cancel":
      return state.phase === "waiting" || state.phase === "starting" ? { phase: "cancelled" } : state;
    case "again":
      return { phase: "form" };
  }
}

/** Milliseconds before the next check: quick at first, then easing off. */
export function pollDelay(polls: number): number {
  return Math.min(2000 + polls * 500, 6000);
}

/** Whether the screen should be asking the server right now. */
export function shouldCheck(state: ConnectState, now: number): boolean {
  return state.phase === "waiting" && !state.checking && now - state.startedAt < TIMEOUT_MS;
}

export function isFinished(state: ConnectState): boolean {
  return state.phase === "ready";
}

/** The sentence under the waiting spinner. */
export function waitingLine(state: Extract<ConnectState, { phase: "waiting" }>, now: number): string {
  const seconds = Math.max(0, Math.floor((now - state.startedAt) / 1000));
  const minutes = Math.floor(seconds / 60);
  const spent = minutes >= 1 ? `${minutes} min` : `${seconds} s`;
  const problem = state.lastError ? ` The last check did not work (${state.lastError}). Trying again.` : "";
  return `Waiting for you to finish there. Looked ${state.polls} ${state.polls === 1 ? "time" : "times"} in ${spent}.${problem}`;
}

// --- names -------------------------------------------------------------------------

export const ACCOUNT_SLUG = /^[a-z][a-z0-9_]{0,23}$/;
export const PROVIDER_ID = /^[a-z][a-z0-9_-]{0,39}$/;
export const LABEL_MAX = 80;

/** A short account name from words: "Work mail" becomes "work_mail". */
export function accountSlug(words: string): string {
  const slug = words.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const letters = /^[a-z]/.test(slug) ? slug : slug ? `a_${slug}` : "";
  return letters.slice(0, 24).replace(/_+$/, "");
}

export function providerSlug(words: string): string {
  const slug = words.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const letters = /^[a-z]/.test(slug) ? slug : slug ? `p-${slug}` : "";
  return letters.slice(0, 40).replace(/-+$/, "");
}

export function accountNameProblem(slug: string, taken: readonly string[]): string | null {
  if (!slug) return "Give the account a short name, such as mail.";
  if (!ACCOUNT_SLUG.test(slug)) return "A short name starts with a letter and uses only lower-case letters, digits and _ (24 at most).";
  if (taken.includes(slug)) return `There is already an account called "${slug}". Choose another short name, or forget that one first.`;
  return null;
}

export function labelProblem(label: string, what = "name"): string | null {
  if (!label.trim()) return `Give it a ${what}.`;
  if (label.trim().length > LABEL_MAX) return `Keep the ${what} to ${LABEL_MAX} characters or fewer.`;
  return null;
}

export function providerIdProblem(id: string, taken: readonly string[]): string | null {
  if (id && !PROVIDER_ID.test(id)) return "An id starts with a letter and uses only lower-case letters, digits, - and _ (40 at most).";
  if (id && taken.includes(id)) return `There is already a provider with the id "${id}". Choose another.`;
  return null;
}

/** A key must be plain visible characters: it travels in a header. Mirrors the server. */
export function keyProblem(key: string): string | null {
  const value = key.trim();
  if (!value) return "Type the key first.";
  if (value.length > 8192) return "That key is too long.";
  for (const ch of value) {
    const code = ch.charCodeAt(0);
    if (code < 0x21 || code > 0x7e) return "A key cannot contain spaces or unusual characters. Copy it again from the provider.";
  }
  return null;
}

/** The name of a saved secret: lower-case letters, digits, - and _. */
export function secretNameProblem(name: string, taken: readonly string[]): string | null {
  if (!/^[a-z][a-z0-9_-]{0,39}$/.test(name)) return "A secret's name starts with a letter and uses lower-case letters, digits, - and _.";
  if (name === "key") return "The provider's own key is saved with its key field.";
  if (taken.includes(name)) return `A secret called "${name}" is saved already. Replace it from its row.`;
  return null;
}
