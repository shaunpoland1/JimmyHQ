import assert from "node:assert/strict";
import { test } from "node:test";
import {
  IDLE,
  MIN_SIZE_MARGIN_BYTES,
  elapsedMs,
  escapeCancels,
  isCancellable,
  nearSizeLimit,
  remainingMs,
  step,
  stopTime,
} from "../src/modules/speech/lib/recorder.ts";
import type { Problem, RecorderEvent, RecorderState } from "../src/modules/speech/lib/recorder.ts";

const MB = 1024 * 1024;
const REQUESTING: RecorderState = { phase: "requesting" };
const RECORDING: RecorderState = { phase: "recording", startedAt: 1_000, stopAt: 300_000, maxBytes: 25 * MB };
const UPLOADING: RecorderState = { phase: "uploading", reason: "pressed" };
const plain: Problem = { code: "denied", message: "The microphone is blocked.", retry: false };
const busy: Problem = { code: "busy", message: "The speech model is busy.", retry: true };
const FAILED: RecorderState = { phase: "error", problem: plain };
const HELD: RecorderState = { phase: "error", problem: busy };

const started: RecorderEvent = { type: "started", at: 1_000, maxSeconds: 300, maxBytes: 25 * MB };
const quietTick: RecorderEvent = { type: "tick", at: 5_000, bytes: 40_000, largestChunk: 8_000 };

test("pressing an idle button asks for the microphone", () => {
  assert.deepEqual(step(IDLE, { type: "press" }), { state: REQUESTING, effects: ["open"] });
});

test("an idle button ignores everything but a press", () => {
  const events: RecorderEvent[] = [
    { type: "cancel" },
    started,
    { type: "refused", problem: plain },
    quietTick,
    { type: "lost" },
    { type: "transcribed" },
    { type: "failed", problem: plain },
  ];
  for (const event of events) assert.deepEqual(step(IDLE, event), { state: IDLE, effects: [] }, event.type);
});

test("recording begins when the microphone opens, with the automatic stop set just inside the server's limit", () => {
  const result = step(REQUESTING, started);
  assert.deepEqual(result, {
    state: { phase: "recording", startedAt: 1_000, stopAt: 1_000 + 299_000, maxBytes: 25 * MB },
    effects: [],
  });
});

test("a refusal while asking shows the problem and records nothing", () => {
  assert.deepEqual(step(REQUESTING, { type: "refused", problem: plain }), { state: FAILED, effects: [] });
});

test("cancelling while asking returns to idle and releases whatever was opened", () => {
  assert.deepEqual(step(REQUESTING, { type: "cancel" }), { state: IDLE, effects: ["discard"] });
});

test("a second press while asking does nothing", () => {
  assert.deepEqual(step(REQUESTING, { type: "press" }), { state: REQUESTING, effects: [] });
  assert.deepEqual(step(REQUESTING, quietTick), { state: REQUESTING, effects: [] });
  assert.deepEqual(step(REQUESTING, { type: "transcribed" }), { state: REQUESTING, effects: [] });
});

test("pressing while recording stops and uploads", () => {
  assert.deepEqual(step(RECORDING, { type: "press" }), { state: UPLOADING, effects: ["finish"] });
});

test("cancelling a recording discards it without uploading", () => {
  const result = step(RECORDING, { type: "cancel" });
  assert.deepEqual(result, { state: IDLE, effects: ["discard"] });
  assert.ok(!result.effects.includes("finish") && !result.effects.includes("upload"));
});

test("a tick inside the limits keeps recording", () => {
  const result = step(RECORDING, quietTick);
  assert.equal(result.state, RECORDING);
  assert.deepEqual(result.effects, []);
});

test("recording stops by itself at the time limit and uploads what it has", () => {
  assert.deepEqual(step(RECORDING, { type: "tick", at: 299_999, bytes: 1, largestChunk: 1 }).state, RECORDING);
  assert.deepEqual(step(RECORDING, { type: "tick", at: 300_000, bytes: 1, largestChunk: 1 }), {
    state: { phase: "uploading", reason: "time" },
    effects: ["finish"],
  });
});

test("recording stops by itself before the size limit is passed", () => {
  const nearly = 25 * MB - 10_000;
  assert.deepEqual(step(RECORDING, { type: "tick", at: 5_000, bytes: nearly, largestChunk: 8_000 }), {
    state: { phase: "uploading", reason: "size" },
    effects: ["finish"],
  });
});

test("a microphone that goes away mid-recording sends what was recorded", () => {
  assert.deepEqual(step(RECORDING, { type: "lost" }), { state: { phase: "uploading", reason: "lost" }, effects: ["finish"] });
});

test("a recorder failure shows the problem, releases the microphone and keeps nothing to retry", () => {
  assert.deepEqual(step(RECORDING, { type: "failed", problem: busy }), {
    state: { phase: "error", problem: { ...busy, retry: false } },
    effects: ["discard"],
  });
});

test("a recording ignores events that belong to other phases", () => {
  for (const event of [started, { type: "refused", problem: plain }, { type: "transcribed" }] as RecorderEvent[]) {
    assert.deepEqual(step(RECORDING, event), { state: RECORDING, effects: [] }, event.type);
  }
});

test("a transcription returns the button to idle", () => {
  assert.deepEqual(step(UPLOADING, { type: "transcribed" }), { state: IDLE, effects: [] });
});

test("a failed upload shows the problem", () => {
  assert.deepEqual(step(UPLOADING, { type: "failed", problem: busy }), { state: HELD, effects: [] });
  assert.deepEqual(step(UPLOADING, { type: "failed", problem: plain }), { state: FAILED, effects: [] });
});

test("cancelling an upload abandons it", () => {
  assert.deepEqual(step(UPLOADING, { type: "cancel" }), { state: IDLE, effects: ["discard"] });
});

test("presses, ticks and a lost microphone are ignored while uploading", () => {
  for (const event of [{ type: "press" }, quietTick, { type: "lost" }, started] as RecorderEvent[]) {
    assert.deepEqual(step(UPLOADING, event), { state: UPLOADING, effects: [] }, event.type);
  }
});

test("pressing after a plain failure starts a new recording", () => {
  assert.deepEqual(step(FAILED, { type: "press" }), { state: REQUESTING, effects: ["open"] });
});

test("pressing after a busy answer sends the kept recording again without recording anew", () => {
  assert.deepEqual(step(HELD, { type: "press" }), { state: { phase: "uploading", reason: "retry" }, effects: ["upload"] });
});

test("cancelling after a failure drops the kept recording", () => {
  assert.deepEqual(step(HELD, { type: "cancel" }), { state: IDLE, effects: ["discard"] });
  assert.deepEqual(step(FAILED, { type: "cancel" }), { state: IDLE, effects: ["discard"] });
});

test("a failed button ignores late events from the attempt that failed", () => {
  for (const event of [started, quietTick, { type: "lost" }, { type: "transcribed" }, { type: "failed", problem: busy }] as RecorderEvent[]) {
    assert.deepEqual(step(FAILED, event), { state: FAILED, effects: [] }, event.type);
  }
});

test("a whole dictation: press, record, press, text", () => {
  let state = IDLE;
  const seen: string[] = [];
  const events: RecorderEvent[] = [{ type: "press" }, started, quietTick, { type: "press" }, { type: "transcribed" }];
  for (const event of events) {
    const result = step(state, event);
    state = result.state;
    seen.push(...result.effects);
  }
  assert.deepEqual(state, IDLE);
  assert.deepEqual(seen, ["open", "finish"]);
});

test("the automatic stop leaves a margin and never lands before one second", () => {
  assert.equal(stopTime(0, 300), 299_000);
  assert.equal(stopTime(500, 5), 4_500);
  assert.equal(stopTime(0, 1), 1_000);
});

test("the size margin grows with the size of the pieces the recorder produces", () => {
  assert.equal(nearSizeLimit(MB - MIN_SIZE_MARGIN_BYTES - 1, 100, MB), false);
  assert.equal(nearSizeLimit(MB - MIN_SIZE_MARGIN_BYTES, 100, MB), true);
  assert.equal(nearSizeLimit(MB - 150_000, 100_000, MB), true);
  assert.equal(nearSizeLimit(MB - 250_000, 100_000, MB), false);
});

test("escape has something to cancel except when idle or after a plain failure", () => {
  assert.equal(isCancellable(IDLE), false);
  assert.equal(isCancellable(REQUESTING), true);
  assert.equal(isCancellable(RECORDING), true);
  assert.equal(isCancellable(UPLOADING), true);
  assert.equal(isCancellable(HELD), true);
  assert.equal(isCancellable(FAILED), false);
});

test("the clock counts from the start and the countdown to the automatic stop", () => {
  assert.equal(elapsedMs(RECORDING, 8_500), 7_500);
  assert.equal(elapsedMs(RECORDING, 0), 0);
  assert.equal(elapsedMs(IDLE, 8_500), 0);
  assert.equal(remainingMs(RECORDING, 295_000), 5_000);
  assert.equal(remainingMs(RECORDING, 400_000), 0);
  assert.equal(remainingMs(UPLOADING, 1), 0);
});

test("escape cancels a recording, but not under a dialog, not mid-composition and not for other keys", () => {
  assert.equal(escapeCancels(RECORDING, { key: "Escape" }, false), true);
  assert.equal(escapeCancels(HELD, { key: "Escape" }, false), true);
  // A dialog opened over the button keeps its own Escape, and the recording carries on.
  assert.equal(escapeCancels(RECORDING, { key: "Escape" }, true), false);
  assert.equal(escapeCancels(HELD, { key: "Escape" }, true), false);
  assert.equal(escapeCancels(RECORDING, { key: "Escape", isComposing: true }, false), false);
  assert.equal(escapeCancels(RECORDING, { key: "Enter" }, false), false);
  assert.equal(escapeCancels(IDLE, { key: "Escape" }, false), false);
  assert.equal(escapeCancels(FAILED, { key: "Escape" }, false), false);
});
