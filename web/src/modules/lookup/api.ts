// The web lookup module's API client. See docs/lookup.md for the routes.

import { request } from "../../api";

export type LookupProvider = { id: string; label: string; needs_key: boolean; key_set: boolean };

export type LookupStatus = {
  /** Whether a model may search or read pages at all (the setting `lookup.enabled`). */
  enabled: boolean;
  provider: string;
  provider_label: string;
  needs_key: boolean;
  key_set: boolean;
  /** A search can be made with what is set up now. */
  search_ready: boolean;
  /** The sentence that says what to set up, or null. */
  search_problem: string | null;
  chat_default: boolean;
  providers: LookupProvider[];
};

export type TryResult = {
  provider: string;
  provider_label: string;
  results: { title: string; url: string; snippet: string; text: string | null }[];
  duration_ms: number;
};

export const LOOKUP_STATUS_KEY = ["lookup", "status"] as const;

function providerPath(id: string): string {
  return `/api/lookup/providers/${encodeURIComponent(id)}/key`;
}

export const lookupApi = {
  status: () => request<LookupStatus>("GET", "/api/lookup/status"),
  /** Saves a provider's key. The key is never read back. Answers with the new status. */
  saveKey: (id: string, key: string) => request<LookupStatus>("PUT", providerPath(id), { body: { key } }),
  clearKey: (id: string) => request<LookupStatus>("DELETE", providerPath(id)),
  /** Runs one search with what is set up now. It works before web lookup is switched on. */
  trySearch: (query: string) => request<TryResult>("POST", "/api/lookup/try", { body: { query } }),
};
