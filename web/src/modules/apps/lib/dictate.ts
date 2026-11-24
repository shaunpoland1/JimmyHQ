// `hq.speech.dictate({hint?})` and `hq.speech.available()`: an app's page asks the owner
// to dictate. Framework-free, so each rule is tested from the attacker's side
// (web/tests/apps.dictate.test.ts).
//
// The frame has no microphone (`FRAME_ALLOW` is empty) and that does not change. The
// shell records instead, with its own recorder (modules/speech/dictation.ts), on its own
// surface drawn over the app, and sends the recording to `/api/speech/transcribe` with
// `app=<id>`, where the server applies the app's rules again. The page gets back one
// thing, `{text}`, once the words are ready. It never gets the audio, never learns that
// recording started or stopped, and cannot stop, start or watch it: the owner does that
// with Stop and Cancel on the surface, or Escape.
//
// What a page can and cannot do with it:
//   - Only with the `speech.dictate` permission (the relay's usual refusal otherwise).
//   - Only on the owner's touch inside its frame, the same rule as `ui.save`
//     (lib/saving.ts, lib/touch.ts): within SAVE_WINDOW_MS of the touch, and one
//     dictation per touch.
//   - One recording at a time across the whole shell: a second call while any recording
//     is open (another app's, or one of the shell's own dictate buttons) is refused.
//   - `hint` is a short line shown on the surface as plain text: control and direction
//     characters are removed and it is cut to DICTATE_HINT_MAX characters.
//   - A private app dictates only when the engine in use keeps the words on the owner's
//     own machines (`own_machines` in the speech status).
//   - Cancel rejects with the code `cancelled`, which no other failure uses.

import type { BridgeError, ErrorCode } from "./bridge.ts";
import { saveAllowed } from "./saving.ts";
import { environmentProblem, gate } from "../../speech/lib/problems.ts";
import type { Environment } from "../../speech/lib/problems.ts";
import type { Problem, RecorderState } from "../../speech/lib/recorder.ts";
import type { SpeechStatus } from "../../speech/types.ts";

/** The longest hint the surface shows, in characters. */
export const DICTATE_HINT_MAX = 120;

export const DICTATE_REFUSALS = {
  shape: "You sent speech.dictate something that is not {hint?}, so nothing was recorded. Send {} or {hint: \"...\"} with a short line of text.",
  noGesture: "The owner has not touched your page just now, so nothing was recorded. Call hq.speech.dictate from the owner's own click on a button.",
  tooSoon: "You already asked to dictate for the owner's last touch, so nothing was recorded. One touch is one dictation: ask again from the owner's next click.",
  busy: "A recording is already open in JimmyHQ, so nothing more was recorded. Wait for it to finish and ask again from the owner's next click.",
  cancelled: "The owner cancelled the dictation, so no words were returned. Nothing was kept.",
  failed: (why: string) => `The dictation did not finish, so no words were returned. ${why}`,
  unavailable: (why: string) => `Dictation is not available, so nothing was recorded. ${why}`,
} as const;

/** The same said to the owner, for the page to show as it is. */
export const DICTATE_OWNER_REFUSALS = {
  noGesture: "That took too long, or the click was not in the app, so nothing was recorded. Press the button again.",
  tooSoon: "That was a second dictation straight after the first, so nothing was recorded. Press the button again.",
  busy: "A recording is already open. Finish or cancel it first, then press the button again.",
  cancelled: "Dictation cancelled. Nothing was kept.",
} as const;

export const PRIVATE_NOT_LOCAL =
  "This app is private, and the speech engine in use is not known to be on your own machines, so your words would leave them. " +
  "To dictate here, choose Whisper or Parakeet under Settings, Voice, or, if the speech server is yours, set 'Where the speech server is' to 'It is mine'.";

function refuse(code: ErrorCode, message: string, ownerMessage?: string): BridgeError {
  return ownerMessage ? { code, message, owner_message: ownerMessage } : { code, message };
}

function isObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object") return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/** The name the speech module knows an app's dictation by, for the hosted rule and the receipt. */
export function dictateCaller(appId: string): string {
  return `app.${appId}`;
}

/** A hint made safe to show: one short line of plain text, or "". */
export function cleanHint(raw: unknown): string {
  if (typeof raw !== "string") return "";
  // eslint-disable-next-line no-control-regex
  const clean = raw.slice(0, 4 * DICTATE_HINT_MAX).replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g, " ").replace(/\s+/g, " ").trim();
  const chars = Array.from(clean);
  return chars.length > DICTATE_HINT_MAX ? `${chars.slice(0, DICTATE_HINT_MAX - 1).join("").trimEnd()}…` : clean;
}

export type DictateArgs = { ok: true; hint: string } | { ok: false; error: BridgeError };

/** Reads `{hint?}` and nothing else. */
export function readDictateArgs(args: unknown): DictateArgs {
  if (args === undefined || args === null) return { ok: true, hint: "" };
  if (!isObject(args) || !Object.keys(args).every((key) => key === "hint")) return { ok: false, error: refuse("invalid", DICTATE_REFUSALS.shape) };
  if (args.hint !== undefined && args.hint !== null && typeof args.hint !== "string") return { ok: false, error: refuse("invalid", DICTATE_REFUSALS.shape) };
  return { ok: true, hint: cleanHint(args.hint) };
}

/**
 * Whether a dictation may begin now: the owner's touch, as for `ui.save` (within the
 * window, one per touch), and no recording open anywhere in the shell. Null means go ahead.
 */
export function dictateRefusal(touchedAt: number | null, dictatedAt: number | null, now: number, openRecordings: number): BridgeError | null {
  const touch = saveAllowed(touchedAt, dictatedAt, now);
  if (touch === "noGesture") return refuse("not_allowed", DICTATE_REFUSALS.noGesture, DICTATE_OWNER_REFUSALS.noGesture);
  if (touch === "tooSoon") return refuse("quota", DICTATE_REFUSALS.tooSoon, DICTATE_OWNER_REFUSALS.tooSoon);
  if (openRecordings > 0) return refuse("quota", DICTATE_REFUSALS.busy, DICTATE_OWNER_REFUSALS.busy);
  return null;
}

/** The private-app rule: a private app's words go only to an engine on the owner's own machines. */
export function privateProblem(status: Pick<SpeechStatus, "engine" | "own_machines">, isPrivate: boolean): Problem | null {
  if (!isPrivate || status.engine === "off") return null;
  return status.own_machines === true ? null : { code: "not_allowed", message: PRIVATE_NOT_LOCAL, retry: false };
}

/** Everything that stops this app dictating, from the speech status: off, not ready, the hosted rule, privacy, the browser. */
export function dictateProblem(status: SpeechStatus, app: { id: string; private: boolean }, env: Environment | null): Problem | null {
  return gate(status, dictateCaller(app.id)) ?? privateProblem(status, app.private) ?? (env ? environmentProblem(env) : null);
}

export type Availability = { available: true } | { available: false; reason: string };

/** What `hq.speech.available()` answers: whether the page should offer dictation, and if not why, in words for the owner. */
export function availability(status: SpeechStatus, app: { id: string; private: boolean }, env: Environment | null): Availability {
  const problem = dictateProblem(status, app, env);
  return problem ? { available: false, reason: problem.message } : { available: true };
}

/** The refusal for a dictation that could not begin or finish. */
export function problemError(problem: Problem, began: boolean): BridgeError {
  const code: ErrorCode =
    problem.code === "denied" || problem.code === "not_allowed"
      ? "not_allowed"
      : problem.code === "too_long" || problem.code === "too_large"
        ? "too_large"
        : problem.code === "refused" || problem.code === "empty"
          ? "invalid"
          : "unavailable";
  return refuse(code, began ? DICTATE_REFUSALS.failed(problem.message) : DICTATE_REFUSALS.unavailable(problem.message), problem.message);
}

export function cancelledError(): BridgeError {
  return refuse("cancelled", DICTATE_REFUSALS.cancelled, DICTATE_OWNER_REFUSALS.cancelled);
}

// ---------------------------------------------------------------- the desk

/** The shell's recorder, as the desk drives it (modules/speech/dictation.ts is one). */
export type DictateRecorder = {
  getState: () => RecorderState;
  subscribe: (listener: () => void) => () => void;
  press: () => void;
  cancel: () => void;
  /** True when the engine in use sends the audio off this computer. */
  hosted?: () => boolean;
};

/** What the recording surface shows. Nothing of it reaches the frame. */
export type DictateView = {
  /** The frame that asked: only that frame's screen draws the surface. */
  owner: object;
  appName: string;
  hint: string;
  maxSeconds: number;
  state: RecorderState;
  hosted: boolean;
};

export type DictateRequest = { owner: object; appName: string; hint: string; maxSeconds: number };

export type Desk = {
  /** The open dictation, or null. */
  current: () => DictateView | null;
  subscribe: (listener: () => void) => () => void;
  /**
   * Begins one dictation and resolves `{text}` when its words are ready. Throws at once
   * (a BridgeError) while another recording is open. `make` builds the recorder and is
   * handed `heard`, which its transcription calls with the words.
   */
  open: (request: DictateRequest, make: (heard: (text: string) => void) => DictateRecorder) => Promise<{ text: string }>;
  /** Stop and send, or send again after a failure that kept the recording. */
  stop: () => void;
  /** Cancel the open dictation; with `owner`, only if that frame opened it. */
  cancel: (owner?: object) => void;
};

/**
 * The one place an app's dictation runs, for the whole shell. It turns what the shell's
 * recorder does into the one answer the page gets: `{text}` when the words are ready,
 * `cancelled` when the owner cancels, or the refusal for what went wrong. Nothing is
 * answered while it records, and the answer never carries anything but the words.
 */
export function createDesk(openElsewhere: () => number = () => 0): Desk {
  type Live = { request: DictateRequest; recorder: DictateRecorder; settle: (outcome: { ok: true; text: string } | { ok: false; error: BridgeError }) => void };
  let live: Live | null = null;
  const listeners = new Set<() => void>();
  const view = (): DictateView | null =>
    live ? { owner: live.request.owner, appName: live.request.appName, hint: live.request.hint, maxSeconds: live.request.maxSeconds, state: live.recorder.getState(), hosted: live.recorder.hosted?.() ?? false } : null;
  // The same object until something changes, so a screen can hold on to it.
  let snapshot: DictateView | null = null;
  const changed = () => {
    snapshot = view();
    for (const listener of listeners) listener();
  };

  return {
    current: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    open(request, make) {
      if (live || openElsewhere() > 0) throw refuse("quota", DICTATE_REFUSALS.busy, DICTATE_OWNER_REFUSALS.busy);
      let heard: string | null = null;
      let began = false;
      let done = false;
      let stopListening: () => void = () => undefined;
      let resolve: (value: { text: string }) => void = () => undefined;
      let reject: (error: BridgeError) => void = () => undefined;
      const answer = new Promise<{ text: string }>((yes, no) => {
        resolve = yes;
        reject = no;
      });
      const recorder = make((text) => {
        heard = typeof text === "string" ? text.trim() : "";
      });
      const mine: Live = {
        request,
        recorder,
        settle(outcome) {
          if (done) return;
          done = true;
          stopListening();
          if (live === mine) live = null;
          if (outcome.ok) resolve({ text: outcome.text });
          else reject(outcome.error);
          changed();
        },
      };
      live = mine;
      let before = recorder.getState();
      stopListening = recorder.subscribe(() => {
        const state = recorder.getState();
        const was = before;
        before = state;
        if (state.phase === "recording") began = true;
        if (state.phase === "idle" && was.phase !== "idle") {
          // Only the transcription brings a recorder back to idle by itself: a cancel is the desk's own.
          mine.settle({ ok: true, text: heard ?? "" });
          return;
        }
        if (state.phase === "error" && !state.problem.retry) {
          mine.settle({ ok: false, error: problemError(state.problem, began) });
          recorder.cancel();
          return;
        }
        changed();
      });
      changed();
      recorder.press();
      return answer;
    },
    stop() {
      const state = live?.recorder.getState();
      if (!live || !state) return;
      if (state.phase === "recording" || (state.phase === "error" && state.problem.retry)) live.recorder.press();
    },
    cancel(owner) {
      const current = live;
      if (!current || (owner !== undefined && current.request.owner !== owner)) return;
      current.settle({ ok: false, error: cancelledError() });
      current.recorder.cancel();
    },
  };
}
