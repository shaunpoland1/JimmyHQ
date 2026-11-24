import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { formatTapeUsage, joinUsage } from "../lib/format";
import { useModelUsageText } from "../modules/models/useModelUsageText";

type UsageLineProps = {
  /** Model usage text; renders nothing until data exists. */
  model?: string | null;
  /** Plan usage text; renders nothing until data exists. */
  plan?: string | null;
};

/** The mono usage line in the top strip, e.g. "TAPE 1,234 events · 0.4 MB". */
export function UsageLine({ model, plan = null }: UsageLineProps) {
  const modelUsage = useModelUsageText();
  const stats = useQuery({ queryKey: ["tape", "stats"], queryFn: api.tapeStats, refetchInterval: 30_000 });
  const text = joinUsage([stats.data ? formatTapeUsage(stats.data) : null, model ?? modelUsage, plan]);
  return (
    <p className="min-w-0 flex-1 truncate font-mono text-[11px] text-text-muted" aria-label="Usage" title={text || undefined}>
      {text}
    </p>
  );
}
