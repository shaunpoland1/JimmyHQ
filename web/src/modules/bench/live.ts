// Bench events from the app's shared live feed.

import type { TapeEvent } from "../../api";
import { subscribeFeed } from "../../lib/feed";

export type BenchFeedListener = {
  /** A `bench.*` event arrived. */
  onEvent: (event: TapeEvent) => void;
  /** The feed came back after a drop, so anything may have been missed. */
  onReconnect: () => void;
};

/** Listens for Bench events on the live feed. Returns the function that stops listening. */
export function subscribeBenchFeed(listener: BenchFeedListener): () => void {
  return subscribeFeed({
    onEvent: (event) => {
      if (event.kind.startsWith("bench.")) listener.onEvent(event);
    },
    onReconnect: listener.onReconnect,
  });
}
