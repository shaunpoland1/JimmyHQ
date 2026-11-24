// Tape events from the app's shared live feed, for the apps module.

import { subscribeFeed } from "../../lib/feed";
import type { TapeEvent } from "../../api";

type Listener = { onEvent: (event: TapeEvent) => void; onReconnect: () => void };

/** Listens to Tape events as they are printed. Returns the function that stops listening. */
export function subscribeTape(listener: Listener): () => void {
  return subscribeFeed(listener);
}
