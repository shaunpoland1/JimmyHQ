// The integrations module's cached server data, kept current from the live feed.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { subscribeFeed } from "../../lib/feed";
import { handsApi } from "./api";
import type { PreflightBody } from "./api";
import { staleAfter } from "./lib/live";
import type { HandsData } from "./lib/live";

export const HANDS_KEYS = {
  all: ["hands"] as const,
  types: ["hands", "types"] as const,
  providers: ["hands", "providers"] as const,
  accounts: ["hands", "accounts"] as const,
  actions: (account: string) => ["hands", "actions", account] as const,
  actionsAll: ["hands", "actions"] as const,
  rules: (agent: string) => ["hands", "rules", agent] as const,
  rulesAll: ["hands", "rules"] as const,
  preflight: (body: PreflightBody | null) => ["hands", "rules", "preflight", body] as const,
  dependents: (account: string) => ["hands", "accounts", "dependents", account] as const,
  runs: (agent: string) => ["hands", "runs", agent] as const,
  runsAll: ["hands", "runs"] as const,
};

const KEY_OF: Record<HandsData, readonly string[]> = {
  providers: HANDS_KEYS.providers,
  accounts: HANDS_KEYS.accounts,
  rules: HANDS_KEYS.rulesAll,
  runs: HANDS_KEYS.runsAll,
  actions: HANDS_KEYS.actionsAll,
};

/** Events arrive in bursts (a call prints several): wait this long before refetching. */
const SETTLE_MS = 300;

export function useTypesQuery() {
  return useQuery({ queryKey: HANDS_KEYS.types, queryFn: handsApi.types, staleTime: 60_000 });
}

export function useProvidersQuery() {
  return useQuery({ queryKey: HANDS_KEYS.providers, queryFn: handsApi.providers });
}

export function useAccountsQuery() {
  return useQuery({ queryKey: HANDS_KEYS.accounts, queryFn: handsApi.accounts });
}

/** What one account can do. A failure is the provider not answering: say so, do not retry in a loop. */
export function useActionsQuery(account: string, enabled = true) {
  return useQuery({
    queryKey: HANDS_KEYS.actions(account),
    queryFn: () => handsApi.actions(account),
    enabled: enabled && account !== "",
    retry: false,
    staleTime: 30_000,
  });
}

export function useRulesQuery(agent: string) {
  return useQuery({ queryKey: HANDS_KEYS.rules(agent), queryFn: () => handsApi.rules(agent) });
}

/** Which agents' rules depend on an account, read only when it is about to be forgotten. */
export function useDependentsQuery(account: string) {
  return useQuery({ queryKey: HANDS_KEYS.dependents(account), queryFn: () => handsApi.dependents(account), retry: false, staleTime: 0 });
}

/** Which levels a rule may take, asked as the form changes. Null asks nothing. */
export function usePreflightQuery(body: PreflightBody | null) {
  return useQuery({
    queryKey: HANDS_KEYS.preflight(body),
    queryFn: () => handsApi.preflight(body as PreflightBody),
    enabled: body !== null,
    retry: false,
    staleTime: 10_000,
  });
}

export function useRunsQuery(agent: string) {
  return useQuery({ queryKey: HANDS_KEYS.runs(agent), queryFn: () => handsApi.runs(agent) });
}

// Several components ask for live updates at once. They share one listener.
let users = 0;
let stop: (() => void) | null = null;

/** Refetches the integrations data that a live Tape event makes stale. */
export function useHandsLive(): void {
  const client = useQueryClient();
  useEffect(() => {
    users += 1;
    if (users === 1) {
      const pending = new Set<HandsData>();
      let timer: ReturnType<typeof setTimeout> | null = null;
      const flush = () => {
        timer = null;
        pending.forEach((part) => void client.invalidateQueries({ queryKey: KEY_OF[part] }));
        pending.clear();
      };
      const off = subscribeFeed({
        onEvent: (event) => {
          const parts = staleAfter(event.kind);
          if (parts.length === 0) return;
          parts.forEach((part) => pending.add(part));
          if (timer === null) timer = setTimeout(flush, SETTLE_MS);
        },
        onReconnect: () => void client.invalidateQueries({ queryKey: HANDS_KEYS.all }),
      });
      stop = () => {
        off();
        if (timer !== null) clearTimeout(timer);
      };
    }
    return () => {
      users -= 1;
      if (users === 0) {
        stop?.();
        stop = null;
      }
    };
  }, [client]);
}
