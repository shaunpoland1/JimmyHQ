// Runs the recorder state machine against the real microphone and the server.
// No React in here: `useDictation` wraps it for components.

import { ApiError, errorDetail } from "../../api";
import {
  RECORDER_FAILED,
  UNSUPPORTED_RECORDING,
  baseType,
  environmentProblem,
  gate,
  levelFromSamples,
  microphoneProblem,
  pickRecordingType,
  noticeAction,
  problemNotice,
  recordingFilename,
  recordingProblem,
  stopNotice,
  uploadProblem,
} from "./lib/problems";
import { OPEN_RECORDINGS } from "./lib/open";
import { IDLE, step } from "./lib/recorder";
import type { Problem, RecorderEffect, RecorderEvent, RecorderState } from "./lib/recorder";
import type { SpeechLimits, SpeechStatus, Transcript } from "./types";

export type DictationDeps = {
  /** The server's status; `fresh` skips the cache. */
  status: (fresh: boolean) => Promise<SpeechStatus>;
  transcribe: (audio: Blob, filename: string, caller: string, signal: AbortSignal) => Promise<Transcript>;
  /** Shows a toast; `action` puts one button on it. */
  notify: (kind: "info" | "error", message: string, action?: { label: string; run: () => void }) => void;
  /** Called after an upload the server refused, so cached status is not trusted. */
  statusChanged: () => void;
  caller: () => string;
  onText: (text: string) => void;
  /** A shorter longest recording than the server's, in seconds (an app's dictation). */
  maxSeconds?: () => number | null;
  /** A further reason not to record, checked with the server's status before the microphone opens. */
  gate?: (status: SpeechStatus) => Problem | null;
};

export type Dictation = {
  getState: () => RecorderState;
  subscribe: (listener: () => void) => () => void;
  press: () => void;
  /** Stops everything, releases the microphone and drops any recording. */
  cancel: () => void;
  /** Receives the microphone level (0 to 1) about sixty times a second while recording. */
  setLevelListener: (listener: ((level: number) => void) | null) => void;
  /** True when the engine in use sends audio off this computer. */
  hosted: () => boolean;
};

type Capture = {
  stream: MediaStream;
  recorder: MediaRecorder;
  type: string;
  chunks: Blob[];
  bytes: number;
  largestChunk: number;
  limits: SpeechLimits;
  stopMeter: () => void;
};

type Held = { blob: Blob; filename: string; limits: SpeechLimits };

const TICK_MS = 250;
const SLICE_MS = 1000;

function stopTracks(stream: MediaStream): void {
  for (const track of stream.getTracks()) {
    track.onended = null;
    try {
      track.stop();
    } catch {
      // Already stopped.
    }
  }
}

function startMeter(stream: MediaStream, report: (level: number) => void): () => void {
  const Context =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Context) return () => undefined;
  let context: AudioContext;
  let frame = 0;
  try {
    context = new Context();
    // A context made after the press has been awaited can start suspended, which reads as silence.
    if (context.state === "suspended") void context.resume().catch(() => undefined);
    const analyser = context.createAnalyser();
    analyser.fftSize = 512;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const read = () => {
      analyser.getByteTimeDomainData(samples);
      report(levelFromSamples(samples));
      frame = requestAnimationFrame(read);
    };
    frame = requestAnimationFrame(read);
  } catch {
    // No meter is better than no dictation.
    return () => undefined;
  }
  return () => {
    cancelAnimationFrame(frame);
    void context.close().catch(() => undefined);
  };
}

export function createDictation(deps: DictationDeps): Dictation {
  let state: RecorderState = IDLE;
  /** Bumped whenever work in progress is abandoned, so late answers are ignored. */
  let generation = 0;
  let capture: Capture | null = null;
  let held: Held | null = null;
  let upload: AbortController | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let levelListener: ((level: number) => void) | null = null;
  let hosted = false;
  const listeners = new Set<() => void>();
  /** This recorder in the page's set of open ones (lib/open.ts). */
  const self = {};

  function dispatch(event: RecorderEvent): void {
    const result = step(state, event);
    if (result.state !== state) {
      state = result.state;
      OPEN_RECORDINGS.note(self, state);
      if (state.phase === "error") {
        const shown = state;
        const label = noticeAction(shown.problem);
        // The button resends only while this same problem is still the one showing.
        const action = label ? { label, run: () => void (state === shown && dispatch({ type: "press" })) } : undefined;
        deps.notify("error", problemNotice(shown.problem), action);
      }
      for (const listener of listeners) listener();
    }
    for (const effect of result.effects) perform(effect);
  }

  function perform(effect: RecorderEffect): void {
    if (effect === "open") void open();
    else if (effect === "finish") void finish();
    else if (effect === "upload") void send();
    else discard();
  }

  function stopTicker(): void {
    if (ticker !== null) clearInterval(ticker);
    ticker = null;
  }

  /** Lets go of the microphone and the recorder without keeping what was recorded. */
  function release(): void {
    stopTicker();
    const current = capture;
    capture = null;
    if (!current) return;
    current.stopMeter();
    current.recorder.ondataavailable = null;
    current.recorder.onerror = null;
    current.recorder.onstop = null;
    try {
      if (current.recorder.state !== "inactive") current.recorder.stop();
    } catch {
      // Already stopped.
    }
    stopTracks(current.stream);
  }

  function discard(): void {
    generation += 1;
    release();
    held = null;
    upload?.abort();
    upload = null;
  }

  async function open(): Promise<void> {
    generation += 1;
    const mine = generation;
    const stale = () => mine !== generation;
    const refuse = (problem: Problem) => dispatch({ type: "refused", problem });
    const caller = deps.caller();

    let status: SpeechStatus;
    try {
      status = await deps.status(false);
      // A refusal from a cached answer is checked again: the settings may just have been fixed.
      if (!stale() && gate(status, caller)) status = await deps.status(true);
    } catch (error) {
      if (!stale()) refuse({ code: "unreachable", message: errorDetail(error), retry: false });
      return;
    }
    if (stale()) return;
    hosted = status.hosted;

    const blocked =
      gate(status, caller) ??
      deps.gate?.(status) ??
      environmentProblem({
        secure: window.isSecureContext,
        microphoneApi: typeof navigator.mediaDevices?.getUserMedia === "function",
        recorderApi: typeof MediaRecorder !== "undefined",
      });
    if (blocked) return refuse(blocked);

    const type = pickRecordingType((candidate) => MediaRecorder.isTypeSupported(candidate), status.accepted_types);
    if (!type) return refuse(UNSUPPORTED_RECORDING);

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (!stale()) refuse(microphoneProblem(error instanceof Error ? error.name : ""));
      return;
    }
    if (stale()) return stopTracks(stream);

    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType: type });
    } catch {
      stopTracks(stream);
      return refuse(UNSUPPORTED_RECORDING);
    }

    const cap = deps.maxSeconds?.();
    const limits =
      typeof cap === "number" && cap > 0 && cap < status.limits.max_seconds ? { ...status.limits, max_seconds: cap } : status.limits;
    const current: Capture = {
      stream,
      recorder,
      type,
      chunks: [],
      bytes: 0,
      largestChunk: 0,
      limits,
      stopMeter: startMeter(stream, (level) => levelListener?.(level)),
    };
    capture = current;
    const tick = () => dispatch({ type: "tick", at: Date.now(), bytes: current.bytes, largestChunk: current.largestChunk });
    recorder.ondataavailable = (event) => {
      if (event.data.size === 0) return;
      current.chunks.push(event.data);
      current.bytes += event.data.size;
      current.largestChunk = Math.max(current.largestChunk, event.data.size);
      if (capture === current && state.phase === "recording") tick();
    };
    recorder.onerror = () => {
      if (capture === current) dispatch({ type: "failed", problem: RECORDER_FAILED });
    };
    for (const track of stream.getAudioTracks()) {
      track.onended = () => {
        if (capture === current) dispatch({ type: "lost" });
      };
    }
    try {
      recorder.start(SLICE_MS);
    } catch {
      release();
      return refuse(UNSUPPORTED_RECORDING);
    }
    dispatch({ type: "started", at: Date.now(), maxSeconds: limits.max_seconds, maxBytes: limits.max_bytes });
    ticker = setInterval(tick, TICK_MS);
  }

  /** Stops the recorder and waits for its last piece. */
  function stopped(current: Capture): Promise<void> {
    return new Promise((resolve) => {
      // A recorder that stopped by itself (the microphone went away) may still owe its last piece.
      if (current.recorder.state === "inactive") return void setTimeout(resolve, 0);
      current.recorder.onstop = () => resolve();
      try {
        current.recorder.stop();
      } catch {
        resolve();
      }
    });
  }

  async function finish(): Promise<void> {
    const mine = generation;
    const current = capture;
    const reason = state.phase === "uploading" ? state.reason : "pressed";
    stopTicker();
    if (!current) return dispatch({ type: "failed", problem: RECORDER_FAILED });
    current.stopMeter();
    // From here the recording is being closed: a late recorder error must not show a failure beside a good upload.
    current.recorder.onerror = null;
    await stopped(current);
    // The microphone is released as soon as the recording ends, before the upload.
    stopTracks(current.stream);
    if (capture === current) capture = null;
    if (mine !== generation) return;

    const blob = new Blob(current.chunks, { type: baseType(current.type) });
    const problem = recordingProblem(blob.size, current.limits.max_bytes);
    if (problem) return dispatch({ type: "failed", problem });
    const notice = stopNotice(reason, current.limits);
    if (notice) deps.notify("info", notice);
    held = { blob, filename: recordingFilename(current.type), limits: current.limits };
    await send();
  }

  async function send(): Promise<void> {
    const mine = generation;
    const recording = held;
    if (!recording) return dispatch({ type: "failed", problem: RECORDER_FAILED });
    const caller = deps.caller();
    const controller = new AbortController();
    upload = controller;
    let result: Transcript;
    try {
      result = await deps.transcribe(recording.blob, recording.filename, caller, controller.signal);
    } catch (error) {
      if (mine !== generation) return;
      upload = null;
      const code = error instanceof ApiError ? error.status : 0;
      let problem = uploadProblem(code, errorDetail(error));
      deps.statusChanged();
      // A signed-out answer: asking through the shared client takes the app to the sign-in screen.
      if (code === 401) void deps.status(true).catch(() => undefined);
      if (code === 503) {
        // "Busy" passes and is worth a retry; "not set up" is not. The status tells them apart.
        try {
          const status = await deps.status(true);
          if (mine !== generation) return;
          problem = gate(status, caller) ?? problem;
        } catch {
          // Keep the server's own sentence.
        }
      }
      if (!problem.retry) held = null;
      dispatch({ type: "failed", problem });
      return;
    }
    if (mine !== generation) return;
    held = null;
    upload = null;
    dispatch({ type: "transcribed" });
    // Outside the try: a caller whose onText throws must not be reported as a failed upload.
    const text = typeof result.text === "string" ? result.text.trim() : "";
    if (text) deps.onText(text);
    else deps.notify("info", "No speech was heard in that recording.");
  }

  return {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    press: () => dispatch({ type: "press" }),
    cancel: () => {
      if (state.phase === "idle") return;
      dispatch({ type: "cancel" });
    },
    setLevelListener: (listener) => {
      levelListener = listener;
    },
    hosted: () => hosted,
  };
}
