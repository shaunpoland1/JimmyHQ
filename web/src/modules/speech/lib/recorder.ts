// The dictation recorder as a state machine with no browser or React in it.
//
// `step` takes the current state and something that happened, and answers the
// next state plus the side effects to run. The controller in `dictation.ts`
// runs those effects against the microphone and the server.

/** What went wrong, in words for the person, and whether the recording is kept for a retry. */
export type Problem = {
  code:
    | "off"
    | "not_ready"
    | "not_allowed"
    | "insecure"
    | "unsupported"
    | "no_microphone"
    | "denied"
    | "microphone"
    | "empty"
    | "too_long"
    | "too_large"
    | "busy"
    | "unreachable"
    | "refused";
  message: string;
  /** True when the recording is still held and pressing again sends it once more. */
  retry: boolean;
};

/** Why a recording stopped and went to the server. */
export type StopReason = "pressed" | "time" | "size" | "lost" | "retry";

export type RecorderState =
  | { phase: "idle" }
  | { phase: "requesting" }
  | { phase: "recording"; startedAt: number; stopAt: number; maxBytes: number }
  | { phase: "uploading"; reason: StopReason }
  | { phase: "error"; problem: Problem };

export type RecorderEvent =
  /** The button was pressed. */
  | { type: "press" }
  /** Escape, the button being disabled, or the button leaving the page. */
  | { type: "cancel" }
  /** The microphone is open and the recorder is running. */
  | { type: "started"; at: number; maxSeconds: number; maxBytes: number }
  /** Recording could not start: dictation is off, no microphone, and so on. */
  | { type: "refused"; problem: Problem }
  /** A regular check while recording: the time now, the bytes so far and the largest piece seen. */
  | { type: "tick"; at: number; bytes: number; largestChunk: number }
  /** The microphone went away while recording (unplugged, permission withdrawn). */
  | { type: "lost" }
  /** The server answered with text. */
  | { type: "transcribed" }
  /** The recording or the upload failed. */
  | { type: "failed"; problem: Problem };

export type RecorderEffect =
  /** Check the server, ask for the microphone, start recording. */
  | "open"
  /** Stop the recorder, release the microphone, send the recording. */
  | "finish"
  /** Send the held recording again. */
  | "upload"
  /** Release the microphone and drop any recording or upload in progress. */
  | "discard";

export type Step = { state: RecorderState; effects: RecorderEffect[] };

export const IDLE: RecorderState = { phase: "idle" };

/**
 * The server refuses a recording that runs past its limit, and stopping takes
 * a moment, so recording stops this long before the limit.
 */
export const STOP_MARGIN_MS = 1000;
/** Room left under the size limit for the recorder's closing piece, at the least. */
export const MIN_SIZE_MARGIN_BYTES = 32 * 1024;

/** When a recording that began at `startedAt` must stop on its own. */
export function stopTime(startedAt: number, maxSeconds: number): number {
  return startedAt + Math.max(1000, maxSeconds * 1000 - STOP_MARGIN_MS);
}

/** True when another piece of audio could take the recording over the size limit. */
export function nearSizeLimit(bytes: number, largestChunk: number, maxBytes: number): boolean {
  const margin = Math.max(MIN_SIZE_MARGIN_BYTES, largestChunk * 2);
  return bytes >= maxBytes - margin;
}

function same(state: RecorderState): Step {
  return { state, effects: [] };
}

export function step(state: RecorderState, event: RecorderEvent): Step {
  switch (state.phase) {
    case "idle":
      if (event.type === "press") return { state: { phase: "requesting" }, effects: ["open"] };
      return same(state);

    case "requesting":
      switch (event.type) {
        case "started":
          return {
            state: {
              phase: "recording",
              startedAt: event.at,
              stopAt: stopTime(event.at, event.maxSeconds),
              maxBytes: event.maxBytes,
            },
            effects: [],
          };
        case "refused":
          return { state: { phase: "error", problem: event.problem }, effects: [] };
        case "cancel":
          return { state: IDLE, effects: ["discard"] };
        default:
          return same(state);
      }

    case "recording":
      switch (event.type) {
        case "press":
          return { state: { phase: "uploading", reason: "pressed" }, effects: ["finish"] };
        case "tick":
          if (event.at >= state.stopAt) return { state: { phase: "uploading", reason: "time" }, effects: ["finish"] };
          if (nearSizeLimit(event.bytes, event.largestChunk, state.maxBytes)) {
            return { state: { phase: "uploading", reason: "size" }, effects: ["finish"] };
          }
          return same(state);
        case "lost":
          return { state: { phase: "uploading", reason: "lost" }, effects: ["finish"] };
        case "failed":
          return { state: { phase: "error", problem: { ...event.problem, retry: false } }, effects: ["discard"] };
        case "cancel":
          return { state: IDLE, effects: ["discard"] };
        default:
          return same(state);
      }

    case "uploading":
      switch (event.type) {
        case "transcribed":
          return { state: IDLE, effects: [] };
        case "failed":
          return { state: { phase: "error", problem: event.problem }, effects: [] };
        case "cancel":
          return { state: IDLE, effects: ["discard"] };
        default:
          return same(state);
      }

    case "error":
      switch (event.type) {
        case "press":
          return state.problem.retry
            ? { state: { phase: "uploading", reason: "retry" }, effects: ["upload"] }
            : { state: { phase: "requesting" }, effects: ["open"] };
        case "cancel":
          return { state: IDLE, effects: ["discard"] };
        default:
          return same(state);
      }
  }
}

/** True while there is something Escape would cancel or discard. */
export function isCancellable(state: RecorderState): boolean {
  if (state.phase === "idle") return false;
  if (state.phase === "error") return state.problem.retry;
  return true;
}

/**
 * Whether a key press should cancel this recorder. Only Escape does, never
 * while a word is being composed, and never when a dialog that does not hold
 * the button is open on top of it: that Escape belongs to the dialog.
 */
export function escapeCancels(
  state: RecorderState,
  key: { key: string; isComposing?: boolean },
  underDialog: boolean,
): boolean {
  return key.key === "Escape" && !key.isComposing && !underDialog && isCancellable(state);
}

/** Elapsed recording time for the button's clock. */
export function elapsedMs(state: RecorderState, now: number): number {
  return state.phase === "recording" ? Math.max(0, now - state.startedAt) : 0;
}

/** Time left before the automatic stop. */
export function remainingMs(state: RecorderState, now: number): number {
  return state.phase === "recording" ? Math.max(0, state.stopAt - now) : 0;
}
