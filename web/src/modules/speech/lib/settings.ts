// Logic for the Voice settings card. No browser or React in here.

import type { SpeechStatus } from "../types.ts";
import { formatDuration, withoutTicks } from "./problems.ts";

export { withoutTicks } from "./problems.ts";

/** The longest key the server accepts. */
export const KEY_MAX_LENGTH = 500;

export type KeyCheck = { ok: true; key: string } | { ok: false; error: string };

/** The same rule the server applies: 1 to 500 printable ASCII characters, no spaces. */
export function checkKey(raw: string): KeyCheck {
  const key = raw.trim();
  if (!key) return { ok: false, error: "Paste the key the speech service gave you." };
  if (key.length > KEY_MAX_LENGTH) return { ok: false, error: `A key can be at most ${KEY_MAX_LENGTH} characters.` };
  if (!/^[\x21-\x7e]+$/.test(key)) {
    return { ok: false, error: "Paste the key exactly as the speech service gave it, with no spaces or line breaks." };
  }
  return { ok: true, key };
}

/** The command inside a server message written as `like this`, or null. */
export function commandIn(message: string | null): string | null {
  if (!message) return null;
  const match = /`([^`]+)`/.exec(message);
  return match?.[1]?.trim() || null;
}

export type Readiness = { tone: "ok" | "warn" | "off"; label: string };

export function readiness(status: Pick<SpeechStatus, "engine" | "ready">): Readiness {
  if (status.engine === "off") return { tone: "off", label: "Off" };
  return status.ready ? { tone: "ok", label: "Ready" } : { tone: "warn", label: "Not ready" };
}

/** One line on where the audio goes. Null when dictation is off. */
export function privacyLine(status: Pick<SpeechStatus, "engine" | "hosted">): string | null {
  if (status.engine === "off") return null;
  if (status.hosted) {
    return "Hosted: your audio is sent to a speech server outside your network to be turned into text. Only the parts of JimmyHQ you allow may use it.";
  }
  if (status.engine === "endpoint") {
    return "Local: your audio goes to a speech server on this computer or your own network, and nowhere else.";
  }
  return "Local: your audio is turned into text on this computer and never leaves it.";
}

function megabytes(bytes: number): string {
  const value = bytes / (1024 * 1024);
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** True for an engine whose model runs inside JimmyHQ on this computer, one recording at a time. */
export function runsHere(engine: SpeechStatus["engine"]): boolean {
  return engine === "local" || engine === "parakeet";
}

/** The limits in one sentence or two. */
export function limitsLine(status: Pick<SpeechStatus, "engine" | "limits">): string {
  const { max_seconds, max_bytes, busy_wait_seconds } = status.limits;
  const base = `A recording can be up to ${formatDuration(max_seconds)} long and ${megabytes(max_bytes)} MB.`;
  if (!runsHere(status.engine)) return base;
  return `${base} If the model is busy, a recording waits up to ${formatDuration(busy_wait_seconds)} before it is told to try again.`;
}

type FeedEvent = { kind: string; payload: Record<string, unknown> };

/** True for a tape event after which the speech status may read differently. */
export function affectsSpeechStatus(event: FeedEvent): boolean {
  if (event.kind.startsWith("speech.")) return true;
  if (event.kind === "setting.updated" || event.kind === "setting.reset") {
    const key = event.payload.key;
    return typeof key === "string" && key.startsWith("speech.");
  }
  return false;
}

/** Adds a caller to the allowed list without repeating it. */
export function withCaller(callers: readonly string[], caller: string): string[] {
  const name = caller.trim().toLowerCase();
  return callers.includes(name) ? [...callers] : [...callers, name];
}

/** A download size in words: "670 MB", "2.5 GB". */
export function downloadSize(megabytesTotal: number): string {
  if (megabytesTotal < 1000) return `${Math.round(megabytesTotal)} MB`;
  const gigabytes = megabytesTotal / 1000;
  return `${Number.isInteger(gigabytes) ? gigabytes : gigabytes.toFixed(1)} GB`;
}

export type DownloadState = {
  tone: "ok" | "warn" | "busy";
  /** One or two sentences about the model's files. */
  text: string;
  /** The button's label, or null for no button. */
  action: string | null;
};

/**
 * What the Voice card says about a model that has to be downloaded (Parakeet's),
 * or null for an engine that has no such line.
 */
export function downloadState(status: Pick<SpeechStatus, "ready" | "warm" | "download">): DownloadState | null {
  const download = status.download;
  if (!download) return null;
  const size = download.megabytes ? `about ${downloadSize(download.megabytes)}` : "the model";
  if (download.preparing) {
    return {
      tone: "busy",
      text: `Getting the model ready: downloading ${size} from ${download.host} the first time, then loading it. This can take several minutes.`,
      action: null,
    };
  }
  // Without the add-on nothing can be downloaded; the card's problem line says how to install it.
  const action = (label: string) => (status.ready ? label : null);
  if (download.problem) return { tone: "warn", text: withoutTicks(download.problem), action: action("Try the download again") };
  if (status.warm) return { tone: "ok", text: "The model is downloaded and loaded, ready for the first recording.", action: null };
  if (download.present === true) {
    return {
      tone: "ok",
      text: "The model is downloaded. It is loaded with the first recording, which then takes a few seconds longer.",
      action: action("Load it now"),
    };
  }
  const known = download.present === false ? "The model is not downloaded yet." : "Whether the model is downloaded cannot be told yet.";
  return {
    tone: "warn",
    text: `${known} The first recording downloads ${size} from ${download.host} once, and waits for it. Download it now to have it ready. Only the model comes down; your audio is never uploaded.`,
    action: action("Download the model now"),
  };
}
