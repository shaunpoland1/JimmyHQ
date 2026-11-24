// The speech module's API client. See docs/speech.md for the routes.

import { ApiError, request } from "../../api";
import type { KeyResult, SpeechStatus, Transcript } from "./types";

export type { KeyResult, SpeechDownload, SpeechEngine, SpeechLimits, SpeechStatus, Transcript, TranscriptSegment, TranscriptWord } from "./types";

export type TranscribeOptions = {
  /** Which surface is asking, for the hosted-engine permission rule. */
  caller: string;
  /** The upload's file name; its extension tells the server the format if the type is missing. */
  filename: string;
  /** Two- or three-letter code. Left out, the server uses its setting, else detects. */
  language?: string;
  /** The app whose page asked for this dictation (`hq.speech.dictate`). The server applies that app's rules. */
  app?: string;
  signal?: AbortSignal;
};

function detailOf(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return `Request failed (${status}).`;
}

/**
 * Sends a recording to be transcribed. The shared `request` helper only sends
 * JSON, and a recording goes as multipart, so this one call uses fetch itself
 * and raises the same `ApiError`.
 */
async function transcribe(audio: Blob, options: TranscribeOptions): Promise<Transcript> {
  const form = new FormData();
  form.append("file", audio, options.filename);
  form.append("caller", options.caller);
  if (options.language) form.append("language", options.language);
  if (options.app) form.append("app", options.app);
  let response: Response;
  try {
    response = await fetch("/api/speech/transcribe", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      body: form,
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "Cannot reach the server.");
  }
  if (!response.ok) {
    let parsed: unknown = null;
    try {
      parsed = await response.json();
    } catch {
      parsed = null;
    }
    throw new ApiError(response.status, detailOf(parsed, response.status));
  }
  try {
    return (await response.json()) as Transcript;
  } catch {
    throw new ApiError(response.status, "The server sent an unreadable response.");
  }
}

export const speechApi = {
  status: () => request<SpeechStatus>("GET", "/api/speech/status"),
  /** Starts downloading and loading Parakeet's model in the background; answers the new status. */
  prepare: () => request<SpeechStatus>("POST", "/api/speech/prepare"),
  transcribe,
  saveKey: (key: string) => request<KeyResult>("PUT", "/api/speech/key", { body: { key } }),
  removeKey: () => request<KeyResult>("DELETE", "/api/speech/key"),
};

export const SPEECH_STATUS_KEY = ["speech", "status"] as const;
/** How long a status answer is reused, so a page of dictate buttons asks once. */
export const SPEECH_STATUS_STALE_MS = 30_000;
