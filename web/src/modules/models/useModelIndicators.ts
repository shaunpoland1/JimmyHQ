import { useMemo } from "react";
import { pickIndicators } from "./lib/indicators";
import { useHealthQuery, useModelsLive } from "./queries";

export type StatusIndicator = { id: string; label: string; ok: boolean };

/** Connection indicators for the status bar, one per model provider worth showing. Owned by the models module. */
export function useModelIndicators(): StatusIndicator[] {
  useModelsLive();
  const health = useHealthQuery();
  const entries = health.data?.providers;
  return useMemo(() => (entries ? pickIndicators(entries) : []), [entries]);
}
