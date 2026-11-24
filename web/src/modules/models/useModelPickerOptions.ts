import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";
import { EFFORTS, modelsApi } from "./api";
import { buildPickerOptions } from "./lib/picker";
import type { ModelPickerOptions } from "./lib/picker";
import { missingKey } from "./lib/providers";
import { MODEL_KEYS, useModelsLive, useProvidersQuery, useRolesQuery } from "./queries";

/**
 * Ready-made options for `ModelPicker`, built from the roles, the enabled
 * providers and each one's discovered models. A provider that cannot be
 * listed simply offers no models; its roles still work.
 */
export function useModelPickerOptions(): { options: ModelPickerOptions; loading: boolean } {
  useModelsLive();
  const roles = useRolesQuery();
  const providers = useProvidersQuery();
  // A hosted provider with no key is never contacted, so it has no list to ask for.
  const enabled = useMemo(
    () => (providers.data?.providers ?? []).filter((provider) => provider.enabled && !missingKey(provider)),
    [providers.data],
  );
  const lists = useQueries({
    queries: enabled.map((provider) => ({
      queryKey: MODEL_KEYS.providerModels(provider.id),
      queryFn: () => modelsApi.providerModels(provider.id),
      retry: false,
      staleTime: 60_000,
    })),
  });
  // A stable string, so the options object only changes when a list does.
  const signature = JSON.stringify(lists.map((list) => list.data?.models ?? []));
  const options = useMemo(() => {
    const byProvider: Record<string, readonly string[]> = {};
    (JSON.parse(signature) as string[][]).forEach((models, index) => {
      const provider = enabled[index];
      if (provider) byProvider[provider.id] = models;
    });
    return buildPickerOptions(roles.data?.roles ?? [], providers.data?.providers ?? [], byProvider, EFFORTS);
  }, [roles.data, providers.data, enabled, signature]);
  return { options, loading: roles.isPending || providers.isPending };
}
