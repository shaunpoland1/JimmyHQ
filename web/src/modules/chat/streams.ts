// The replies this tab is listening to, kept outside React so that a reply
// carries on while the owner looks at another conversation or another screen.
//
// Text arrives in many small pieces. They are gathered and shown once per
// animation frame, so a fast stream does not redraw the thread per token.

import type { QueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { ApiError, errorDetail } from "../../api";
import type { ToastApi } from "../../hooks/useToast";
import { onSessionEnd } from "../../lib/session";
import { chatApi } from "./api";
import type { AssistantMessage } from "./api";
import { CHAT_LIST_KEY, conversationKey } from "./keys";
import { applyReply, interpretReplyEvent, newLiveReply } from "./lib/reply";
import { applyStep } from "./lib/web";
import type { LiveReply, ThreadPages } from "./lib/reply";
import { endRuns } from "./lib/runs";
import type { EndableRun } from "./lib/runs";
import { readSse } from "./lib/sse";

/**
 * `buffer` is text that has arrived and is not on screen yet; `stopped` is
 * set when the owner pressed Stop, so an ending without `done` is expected.
 */
type Run = EndableRun;

export type Services = { client: QueryClient; toast: ToastApi };

export type StartResult = { ok: true } | { ok: false; status: number; detail: string };

/** How long after Stop the stream is given to end on its own before the fetch is aborted. */
const STOP_GRACE_MS = 1500;
/** The server notices a dropped connection within about a second, then stores what arrived. */
const SETTLE_MS = 1500;

/** How long a stream is given to close after its last event. */
const CLOSE_GRACE_MS = 2000;

const BUSY = "A reply is already being written in this conversation. Wait for it to finish, or stop it first.";
const DROPPED = "The connection dropped while the reply was being written. Showing what the server kept.";

let state: Readonly<Record<string, LiveReply>> = {};
let ids: ReadonlySet<string> = new Set();
const runs = new Map<string, Run>();
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function put(id: string, next: LiveReply | null): void {
  const had = id in state;
  if (next) state = { ...state, [id]: next };
  else {
    const { [id]: _gone, ...rest } = state;
    state = rest;
  }
  if (had !== (next !== null)) ids = new Set(Object.keys(state));
  listeners.forEach((listener) => listener());
}

function patch(id: string, change: Partial<LiveReply>): void {
  const current = state[id];
  if (current) put(id, { ...current, ...change });
}

function flush(id: string, run: Run): void {
  if (run.frame !== null) cancelAnimationFrame(run.frame);
  run.frame = null;
  if (!run.buffer) return;
  const text = run.buffer;
  run.buffer = "";
  const current = state[id];
  if (current) put(id, { ...current, text: current.text + text });
}

function refresh(client: QueryClient, id: string): void {
  void client.invalidateQueries({ queryKey: conversationKey(id) });
  void client.invalidateQueries({ queryKey: CHAT_LIST_KEY });
}

async function listen(services: Services, id: string, run: Run, body: ReadableStream<Uint8Array>): Promise<void> {
  const { client, toast } = services;
  let ended = false;

  const finish = (message: AssistantMessage | null) => {
    ended = true;
    if (run.frame !== null) cancelAnimationFrame(run.frame);
    run.frame = null;
    const start = state[id]?.start ?? null;
    client.setQueryData<ThreadPages>(conversationKey(id), (data) =>
      applyReply(data, { userMessage: start?.user_message ?? null, replaces: start?.replaces ?? null, message }),
    );
    runs.delete(id);
    put(id, null);
    refresh(client, id);
  };

  try {
    for await (const piece of readSse(body)) {
      if (run.dropped) return;
      // The reply has ended and the server is closing the stream. It is read to its end rather
      // than left: leaving cancels the request, which the browser then reports as failed.
      if (ended) continue;
      const event = interpretReplyEvent(piece);
      if (!event) continue;
      if (event.type === "start") patch(id, { phase: "streaming", start: event.start });
      else if (event.type === "step") {
        const current = state[id];
        if (current) put(id, { ...current, steps: applyStep(current.steps, event.step) });
      } else if (event.type === "delta") {
        run.buffer += event.text;
        run.frame ??= requestAnimationFrame(() => {
          run.frame = null;
          flush(id, run);
        });
      } else if (event.type === "done") finish(event.message);
      else {
        finish(event.message);
        // A failed reply that was saved says why in the thread; one that was not has only this.
        if (!event.message) toast.error(event.detail);
      }
      // If the stream does not close by itself after its last event, it is let go of.
      if (ended) setTimeout(() => run.abort.abort(), CLOSE_GRACE_MS);
    }
  } catch {
    // The connection broke, or the fetch was aborted: handled below.
  }
  // A run let go of when the session ended reports nothing and touches nothing.
  if (ended || run.dropped) return;
  // The stream ended without `done` or `error`. The server holds what arrived.
  flush(id, run);
  runs.delete(id);
  put(id, null);
  if (!run.stopped) toast.error(DROPPED);
  refresh(client, id);
  setTimeout(() => refresh(client, id), SETTLE_MS);
}

/**
 * Sends a message (or asks for a new reply) and starts listening. Resolves
 * once the server has accepted or refused it; the reply itself carries on in
 * the background.
 */
/** `web` is the owner's choice of the Web switch for this one reply. */
export async function startReply(services: Services, id: string, ask: ({ text: string } | { regenerate: true }) & { web?: boolean }): Promise<StartResult> {
  if (runs.has(id)) return { ok: false, status: 409, detail: BUSY };
  const run: Run = { abort: new AbortController(), buffer: "", frame: null, stopped: false, dropped: false };
  runs.set(id, run);
  put(id, newLiveReply("text" in ask ? ask.text : null));
  let body: ReadableStream<Uint8Array>;
  try {
    const choice = ask.web === undefined ? {} : { web: ask.web };
    body = "text" in ask ? await chatApi.send(id, ask.text, run.abort.signal, choice) : await chatApi.regenerate(id, run.abort.signal, choice);
  } catch (error) {
    if (run.dropped) return { ok: false, status: 0, detail: "" };
    runs.delete(id);
    put(id, null);
    refresh(services.client, id);
    // Stopped before the server answered: nothing to report.
    if (!(error instanceof ApiError)) return { ok: false, status: 0, detail: "" };
    return { ok: false, status: error.status, detail: error.detail };
  }
  if (run.dropped) {
    // The session ended while the server was accepting it: do not start reading.
    void body.cancel().catch(() => undefined);
    return { ok: false, status: 0, detail: "" };
  }
  void listen(services, id, run, body);
  return { ok: true };
}

/**
 * Lets go of every reply this tab is listening to: the connections are
 * closed (the server then stops each reply and stores what had arrived),
 * nothing more is shown and no toast is raised. Runs when the session ends,
 * so a reply from one session never carries on behind the sign-in screen or
 * reappears in the next.
 */
export function resetReplies(): void {
  endRuns(runs, (frame) => cancelAnimationFrame(frame));
  state = {};
  ids = new Set();
  listeners.forEach((listener) => listener());
}

onSessionEnd(resetReplies);

/**
 * Stops the reply being written in a conversation, whichever tab asked for
 * it. The server stores what had arrived; this tab's stream then ends itself,
 * and is let go of if it does not.
 */
export async function stopReply(services: Services, id: string): Promise<void> {
  const run = runs.get(id);
  if (run) {
    run.stopped = true;
    patch(id, { stopping: true });
  }
  try {
    await chatApi.stop(id);
  } catch (error) {
    if (run && runs.get(id) === run) run.abort.abort();
    else services.toast.error(errorDetail(error));
    return;
  }
  if (run) {
    setTimeout(() => {
      if (runs.get(id) === run) run.abort.abort();
    }, STOP_GRACE_MS);
  } else refresh(services.client, id);
}

/** The reply this tab is listening to in a conversation, if any. */
export function useLiveReply(id: string | null): LiveReply | undefined {
  return useSyncExternalStore(subscribe, () => (id ? state[id] : undefined));
}

/** The conversations this tab is listening to a reply in. Changes only when one starts or ends. */
export function useLiveIds(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, () => ids);
}
