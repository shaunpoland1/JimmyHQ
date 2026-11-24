// The one connection to the server's live feed, shared by the whole app.
//
// Every part of the app that wants to hear about new Tape events subscribes
// here. Opening a connection each would soon hit the browser's limit of six
// connections per site, and ordinary requests would then stall.

import { FEED_URL } from "../api";
import type { TapeEvent } from "../api";
import { parseFeedMessage } from "./tape";

export type FeedListener = {
  /** A Tape event was printed. */
  onEvent?: (event: TapeEvent) => void;
  /** The feed came back after a drop, so anything may have been missed: refetch. */
  onReconnect?: () => void;
  /** The connection went up or down. */
  onStatus?: (live: boolean) => void;
};

const REOPEN_DELAY_MS = 5000;

const listeners = new Set<FeedListener>();
let source: EventSource | null = null;
let reopening: ReturnType<typeof setTimeout> | null = null;
let live = false;
let dropped = false;

function setLive(next: boolean): void {
  if (live === next) return;
  live = next;
  listeners.forEach((listener) => listener.onStatus?.(next));
}

function open(): void {
  if (source || typeof EventSource === "undefined") return;
  const feed = new EventSource(FEED_URL);
  feed.addEventListener("tape", (message) => {
    const event = parseFeedMessage((message as MessageEvent<string>).data);
    if (event) listeners.forEach((listener) => listener.onEvent?.(event));
  });
  feed.onopen = () => {
    setLive(true);
    if (dropped) listeners.forEach((listener) => listener.onReconnect?.());
    dropped = false;
  };
  feed.onerror = () => {
    setLive(false);
    dropped = true;
    // The browser retries a lost connection itself, but gives up for good when
    // the server answers the retry with an error. Start again in that case.
    if (feed.readyState !== EventSource.CLOSED || source !== feed) return;
    source = null;
    if (reopening) clearTimeout(reopening);
    reopening = setTimeout(() => {
      reopening = null;
      if (listeners.size > 0) open();
    }, REOPEN_DELAY_MS);
  };
  source = feed;
}

function close(): void {
  if (reopening) clearTimeout(reopening);
  reopening = null;
  source?.close();
  source = null;
  live = false;
  dropped = false;
}

/** Listens to the live feed. Returns the function that stops listening. */
export function subscribeFeed(listener: FeedListener): () => void {
  listeners.add(listener);
  open();
  if (live) listener.onStatus?.(true);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) close();
  };
}
