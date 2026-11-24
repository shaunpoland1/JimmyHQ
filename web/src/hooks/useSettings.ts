import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef } from "react";
import { api, errorDetail } from "../api";
import type { SettingResult, SettingValue, SettingsResponse } from "../api";
import { useToast } from "./useToast";

export const SETTINGS_QUERY_KEY = ["settings"] as const;

export function useSettingsQuery() {
  return useQuery({ queryKey: SETTINGS_QUERY_KEY, queryFn: api.settings, staleTime: 30_000 });
}

export type SettingActions = {
  /** Saves a value: optimistic update, rollback and a toast if the server refuses. */
  save: (key: string, value: SettingValue) => Promise<void>;
  /** Resets a setting to its default. */
  reset: (key: string, defaultValue: SettingValue) => Promise<void>;
};

export function useSettingActions(): SettingActions {
  const client = useQueryClient();
  const toast = useToast();
  // Per-key counter so a slow response never overwrites a newer change.
  const sequence = useRef(new Map<string, number>());

  return useMemo(() => {
    const patch = (key: string, value: SettingValue) => {
      client.setQueryData<SettingsResponse>(SETTINGS_QUERY_KEY, (data) =>
        data
          ? { ...data, settings: data.settings.map((item) => (item.key === key ? { ...item, value } : item)) }
          : data,
      );
    };

    const run = async (key: string, optimistic: SettingValue, send: () => Promise<SettingResult>) => {
      const turn = (sequence.current.get(key) ?? 0) + 1;
      sequence.current.set(key, turn);
      await client.cancelQueries({ queryKey: SETTINGS_QUERY_KEY });
      const previous = client
        .getQueryData<SettingsResponse>(SETTINGS_QUERY_KEY)
        ?.settings.find((item) => item.key === key)?.value;
      patch(key, optimistic);
      try {
        const result = await send();
        if (sequence.current.get(key) === turn) patch(key, result.value);
      } catch (error) {
        if (sequence.current.get(key) === turn) {
          if (previous !== undefined) patch(key, previous);
          void client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
        }
        toast.error(errorDetail(error));
      }
    };

    return {
      save: (key, value) => run(key, value, () => api.putSetting(key, value)),
      reset: (key, defaultValue) => run(key, defaultValue, () => api.resetSetting(key)),
    };
  }, [client, toast]);
}
