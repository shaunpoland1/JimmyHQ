// Turning what the server, the browser and the microphone say into one short
// message each. No browser or React in here.

import type { SpeechStatus } from "../types.ts";
import type { Problem, StopReason } from "./recorder.ts";

const HOSTED_SETTING = "Allowed to use a hosted speech server";

/** A server message with the backticks around a command taken off, for plain display. */
export function withoutTicks(message: string): string {
  return message.replace(/`([^`]+)`/g, "$1");
}

/** True when this caller may not use the engine because it is a hosted one. */
export function hostedBlocked(status: Pick<SpeechStatus, "hosted" | "hosted_callers">, caller: string): boolean {
  return status.hosted && !status.hosted_callers.includes(caller.trim().toLowerCase());
}

/**
 * Whether recording should even start, from the server's status. Null means go
 * ahead. Nothing is recorded when this answers a problem.
 */
export function gate(status: SpeechStatus, caller: string): Problem | null {
  if (status.engine === "off") {
    return { code: "off", message: "Dictation is turned off. Turn it on under Settings, Voice.", retry: false };
  }
  if (!status.ready) {
    return {
      code: "not_ready",
      message: withoutTicks(status.problem ?? "Dictation is not ready. Check the settings under Settings, Voice."),
      retry: false,
    };
  }
  if (hostedBlocked(status, caller)) {
    return {
      code: "not_allowed",
      message:
        `The speech server is a hosted one, so your audio would leave this computer, and '${caller}' is not allowed to do that. ` +
        `Nothing was recorded. To allow it, add ${caller} to '${HOSTED_SETTING}' under Settings, Voice.`,
      retry: false,
    };
  }
  return null;
}

export type Environment = {
  /** `window.isSecureContext`. */
  secure: boolean;
  /** `navigator.mediaDevices.getUserMedia` exists. */
  microphoneApi: boolean;
  /** `MediaRecorder` exists. */
  recorderApi: boolean;
};

/** Whether this browser, on this page, can record at all. */
export function environmentProblem(env: Environment): Problem | null {
  if (!env.microphoneApi && !env.secure) {
    return {
      code: "insecure",
      message:
        "The browser only offers the microphone on a secure page. Open JimmyHQ over https, or on this computer as localhost, to dictate.",
      retry: false,
    };
  }
  if (!env.microphoneApi || !env.recorderApi) {
    return { code: "unsupported", message: "This browser cannot record audio, so dictation is not available in it.", retry: false };
  }
  return null;
}

/** The recording containers to try, in order of preference. */
export const RECORDING_TYPES: readonly string[] = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

/** `audio/webm;codecs=opus` becomes `audio/webm`. */
export function baseType(type: string): string {
  return (type.split(";", 1)[0] ?? "").trim().toLowerCase();
}

/** The first container the browser can record and the server accepts, or null. */
export function pickRecordingType(isSupported: (type: string) => boolean, accepted: readonly string[]): string | null {
  const allowed = new Set(accepted.map(baseType));
  for (const type of RECORDING_TYPES) {
    if (!allowed.has(baseType(type))) continue;
    try {
      if (isSupported(type)) return type;
    } catch {
      // A browser that throws for a type it does not know simply does not support it.
    }
  }
  return null;
}

const EXTENSIONS: Readonly<Record<string, string>> = { "audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg" };

/** The upload's file name; the server reads the format from it when the type is missing. */
export function recordingFilename(type: string): string {
  return `dictation.${EXTENSIONS[baseType(type)] ?? "webm"}`;
}

export const UNSUPPORTED_RECORDING: Problem = {
  code: "unsupported",
  message: "This browser cannot record in a format the speech engine accepts, so dictation is not available in it.",
  retry: false,
};

/** What a failed `getUserMedia` means, from the error's name. */
export function microphoneProblem(errorName: string): Problem {
  switch (errorName) {
    case "NotAllowedError":
    case "PermissionDeniedError":
    case "SecurityError":
      return {
        code: "denied",
        message: "The microphone is blocked for this page. Allow it in the browser's site settings, then try again.",
        retry: false,
      };
    case "NotFoundError":
    case "DevicesNotFoundError":
    case "OverconstrainedError":
      return { code: "no_microphone", message: "No microphone was found. Connect one, then try again.", retry: false };
    case "NotReadableError":
    case "TrackStartError":
    case "AbortError":
      return {
        code: "microphone",
        message: "The microphone could not be opened. Another app may be using it. Close that app, then try again.",
        retry: false,
      };
    default:
      return { code: "microphone", message: "The microphone could not be started. Try again.", retry: false };
  }
}

export const RECORDER_FAILED: Problem = {
  code: "microphone",
  message: "The recording stopped unexpectedly. Try again.",
  retry: false,
};

function megabytes(bytes: number): string {
  const value = bytes / (1024 * 1024);
  return value >= 10 || Number.isInteger(value) ? String(Math.round(value)) : value.toFixed(1);
}

/** Checks a finished recording before it is sent. Null means send it. */
export function recordingProblem(bytes: number, maxBytes: number): Problem | null {
  if (bytes <= 0) {
    return { code: "empty", message: "Nothing was recorded. Check the microphone, then try again.", retry: false };
  }
  if (bytes > maxBytes) {
    return {
      code: "too_large",
      message: `That recording is larger than the limit of ${megabytes(maxBytes)} MB. Record a shorter piece, or raise the limit under Settings, Voice.`,
      retry: false,
    };
  }
  return null;
}

/** What a refused or failed upload means. `status` 0 is "could not reach the server". */
export function uploadProblem(status: number, detail: string): Problem {
  const message = withoutTicks(detail) || "The recording could not be transcribed.";
  switch (status) {
    case 0:
      return { code: "unreachable", message, retry: true };
    case 403:
      return { code: "not_allowed", message, retry: false };
    case 409:
      return { code: "off", message: "Dictation is turned off. Turn it on under Settings, Voice.", retry: false };
    case 413:
      // The server's sentence says which limit it was: length or size.
      return { code: /longer/i.test(message) ? "too_long" : "too_large", message, retry: false };
    case 502:
    case 504:
      return { code: "unreachable", message, retry: true };
    case 503:
      return { code: "busy", message, retry: true };
    default:
      return { code: "refused", message, retry: false };
  }
}

/**
 * The label of the button on a problem's toast, or null for no button. A busy
 * engine frees up by itself, so its toast offers to send the kept recording again.
 */
export function noticeAction(problem: Problem): string | null {
  return problem.code === "busy" && problem.retry ? "Retry" : null;
}

/** The text of the toast for a problem, with how to retry when the recording is kept. */
export function problemNotice(problem: Problem): string {
  if (!problem.retry) return problem.message;
  const how = noticeAction(problem) ? "press Retry, or the button again, to resend it" : "press the button again to resend it";
  return `${problem.message} Your recording is kept: ${how}, or Escape to discard it.`;
}

/** The limit in words: "5 minutes", "90 seconds", "1 hour 30 minutes". */
export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const unit = (count: number, name: string) => `${count} ${name}${count === 1 ? "" : "s"}`;
  if (whole < 120 && whole % 60 !== 0) return unit(whole, "second");
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = whole % 60;
  const parts = [hours ? unit(hours, "hour") : "", minutes ? unit(minutes, "minute") : "", rest ? unit(rest, "second") : ""];
  return parts.filter(Boolean).join(" ") || unit(0, "second");
}

/** What to say when a recording was stopped for the person rather than by them. Null for a normal stop. */
export function stopNotice(reason: StopReason, limits: { max_seconds: number; max_bytes: number }): string | null {
  switch (reason) {
    case "time":
      return `Recording stopped at the limit of ${formatDuration(limits.max_seconds)}. Transcribing what was recorded.`;
    case "size":
      return `Recording stopped near the size limit of ${megabytes(limits.max_bytes)} MB. Transcribing what was recorded.`;
    case "lost":
      return "The microphone stopped. Transcribing what was recorded.";
    default:
      return null;
  }
}

/** The recording clock: "0:07", "12:05", "1:02:03". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

/** Loudness from 0 to 1 out of the analyser's byte samples, which rest at 128. */
export function levelFromSamples(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const value = ((samples[index] ?? 128) - 128) / 128;
    sum += value * value;
  }
  const rms = Math.sqrt(sum / samples.length);
  // Ordinary speech sits around 0.05 to 0.25, so stretch it across the meter.
  return Math.min(1, rms * 4);
}

/** Heights (0.15 to 1) for the meter's bars: the middle ones move most. */
export function meterHeights(level: number, bars: number): number[] {
  const clamped = Math.min(1, Math.max(0, level));
  const middle = (bars - 1) / 2;
  return Array.from({ length: bars }, (_, index) => {
    const weight = middle === 0 ? 1 : 1 - (Math.abs(index - middle) / middle) * 0.5;
    return Math.round((0.15 + 0.85 * clamped * weight) * 100) / 100;
  });
}
