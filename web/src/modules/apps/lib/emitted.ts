// What a full app's program emitted for its own open pages (`app.emit` in the SDK), as
// events for that app's frames. The server refuses an event named like one JimmyHQ sends;
// this refuses it again, so a frame is never told something in JimmyHQ's name by an app.

import { EVENTS } from "./bridge.ts";
import type { BridgeEvent } from "./bridge.ts";

const NAME = /^[a-z][a-z0-9_]{0,30}(\.[a-z][a-z0-9_]{0,30}){0,2}$/;
/** How often an open frame of a full app asks for what its program emitted. */
export const EMITTED_POLL_MS = 3000;

export function isAppEventName(name: unknown): name is string {
  return typeof name === "string" && NAME.test(name) && !(EVENTS as readonly string[]).includes(name);
}

/** The answer of GET /api/apps/<id>/emitted, as frame events and where to ask from next. */
export function readEmitted(raw: unknown, after: number): { events: BridgeEvent[]; next: number } {
  const body = raw !== null && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const next = typeof body.next === "number" && Number.isFinite(body.next) && body.next >= after ? body.next : after;
  const list = Array.isArray(body.events) ? body.events : [];
  const events: BridgeEvent[] = [];
  for (const item of list) {
    const entry = item !== null && typeof item === "object" ? (item as Record<string, unknown>) : null;
    if (!entry || !isAppEventName(entry.event)) continue;
    events.push({ event: entry.event, data: entry.data ?? null });
  }
  return { events, next };
}

/** The address to ask; -1 asks only where the list ends now, for a frame that just opened. */
export function emittedPath(appId: string, after: number): string | null {
  if (!/^[a-z][a-z0-9_]{1,30}$/.test(appId)) return null;
  return `/api/apps/${appId}/emitted?after=${Math.max(-1, Math.floor(after))}`;
}
