import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { TapeEvent } from "../api";
import { subscribeFeed } from "../lib/feed";
import { mergeEvents } from "../lib/tape";

export type TapeView = {
  events: TapeEvent[];
  loading: boolean;
  error: boolean;
  /** True while the live feed is connected. */
  live: boolean;
};

/** Recent tape events: one page from `/api/tape`, then live from the feed. */
export function useTape(): TapeView {
  const recent = useQuery({ queryKey: ["tape", "recent"], queryFn: () => api.tape({ limit: 50 }) });
  const [streamed, setStreamed] = useState<TapeEvent[]>([]);
  const [live, setLive] = useState(false);
  const { refetch } = recent;

  useEffect(
    () =>
      subscribeFeed({
        onEvent: (event) => setStreamed((current) => mergeEvents([event], current)),
        // After a reconnect, fetch the page again to fill any gap.
        onReconnect: () => void refetch(),
        onStatus: setLive,
      }),
    [refetch],
  );

  const events = useMemo(() => mergeEvents(streamed, recent.data?.events ?? []), [streamed, recent.data]);

  return { events, loading: recent.isPending, error: recent.isError, live };
}
