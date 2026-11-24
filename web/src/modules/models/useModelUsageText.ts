import { usageText } from "./lib/format";
import { useModelsLive, useUsageQuery } from "./queries";

/** Model usage for the top strip, e.g. "LLM 46.5K calls · 182.8M tok". Owned by the models module. */
export function useModelUsageText(): string | null {
  useModelsLive();
  const usage = useUsageQuery();
  return usageText(usage.data);
}
