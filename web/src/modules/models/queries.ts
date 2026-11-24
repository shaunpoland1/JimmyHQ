// The models module's cached server data, shared by the settings section, the
// top strip and the status bar, and kept current from the live feed.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { modelsApi } from "./api";
import type { HealthResponse } from "./api";
import { staleAfter } from "./lib/events";
import type { ModelData } from "./lib/events";
import { healthPollMs } from "./lib/indicators";
import { subscribeTape } from "./live";

export const MODEL_KEYS = {
  all: ["models"] as const,
  providers: ["models", "providers"] as const,
  roles: ["models", "roles"] as const,
  health: ["models", "health"] as const,
  usage: ["models", "usage"] as const,
  prices: ["models", "prices"] as const,
  providerModels: (id: string) => ["models", "provider-models", id] as const,
};

/** Usage changes with every call; the live feed keeps it fresh, this is the backstop. */
const USAGE_REFRESH_MS = 60_000;
/** Events arrive in bursts (a call prints several); wait this long before refetching. */
const SETTLE_MS = 400;

export function useProvidersQuery() {
  return useQuery({ queryKey: MODEL_KEYS.providers, queryFn: modelsApi.providers });
}

export function useRolesQuery() {
  return useQuery({ queryKey: MODEL_KEYS.roles, queryFn: modelsApi.roles });
}

export function usePricesQuery() {
  return useQuery({ queryKey: MODEL_KEYS.prices, queryFn: modelsApi.prices });
}

export function useUsageQuery() {
  return useQuery({ queryKey: MODEL_KEYS.usage, queryFn: modelsApi.usage, refetchInterval: USAGE_REFRESH_MS });
}

/** Health of the enabled providers, polled at the interval the server suggests. */
export function useHealthQuery() {
  return useQuery<HealthResponse>({
    queryKey: MODEL_KEYS.health,
    queryFn: modelsApi.health,
    refetchInterval: (query) => healthPollMs(query.state.data?.interval_s),
    retry: false,
  });
}

/** A provider's discovered models. An error means it cannot be listed: type the name instead. */
export function useProviderModelsQuery(providerId: string, enabled = true) {
  return useQuery({
    queryKey: MODEL_KEYS.providerModels(providerId),
    queryFn: () => modelsApi.providerModels(providerId),
    enabled: enabled && providerId !== "",
    retry: false,
    staleTime: 60_000,
  });
}

// Several components ask for live updates at once (the top strip, the status
// bar, the settings section). They share one listener, so an event causes one
// round of refetching, not one per component.
let liveUsers = 0;
let stopLive: (() => void) | null = null;

function startLive(client: QueryClient): () => void {
  const pending = new Set<ModelData>();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    timer = null;
    pending.forEach((part) => void client.invalidateQueries({ queryKey: MODEL_KEYS[part] }));
    pending.clear();
  };
  const stop = subscribeTape({
    onEvent: (event) => {
      const parts = staleAfter(event.kind, event.payload);
      if (parts.length === 0) return;
      parts.forEach((part) => pending.add(part));
      if (timer === null) timer = setTimeout(flush, SETTLE_MS);
    },
    onReconnect: () => void client.invalidateQueries({ queryKey: MODEL_KEYS.all }),
  });
  return () => {
    stop();
    if (timer !== null) clearTimeout(timer);
  };
}

/** Refetches the models data that a live Tape event makes stale. */
export function useModelsLive(): void {
  const client = useQueryClient();
  useEffect(() => {
    liveUsers += 1;
    if (liveUsers === 1) stopLive = startLive(client);
    return () => {
      liveUsers -= 1;
      if (liveUsers === 0) {
        stopLive?.();
        stopLive = null;
      }
    };
  }, [client]);
}
