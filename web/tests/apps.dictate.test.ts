// `hq.speech.dictate` and `hq.speech.available`: a page asks the owner to dictate, and the
// shell records on its own surface. These tests are what a page could try with it.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { CALLS, ERROR_CODES, FRAME_ALLOW, createRelay } from "../src/modules/apps/lib/bridge.ts";
import type { BridgeError, BridgeResponse, Grant } from "../src/modules/apps/lib/bridge.ts";
import {
  DICTATE_HINT_MAX,
  DICTATE_REFUSALS,
  PRIVATE_NOT_LOCAL,
  availability,
  cancelledError,
  cleanHint,
  createDesk,
  dictateCaller,
  dictateRefusal,
  privateProblem,
  problemError,
  readDictateArgs,
} from "../src/modules/apps/lib/dictate.ts";
import type { DictateRecorder } from "../src/modules/apps/lib/dictate.ts";
import { SAVE_WINDOW_MS } from "../src/modules/apps/lib/saving.ts";
import { OPEN_RECORDINGS, createOpenSet, holdsRecording } from "../src/modules/speech/lib/open.ts";
import { IDLE, step } from "../src/modules/speech/lib/recorder.ts";
import type { Problem, RecorderEvent, RecorderState } from "../src/modules/speech/lib/recorder.ts";
import type { SpeechStatus } from "../src/modules/speech/types.ts";
// @ts-expect-error The page helper is plain JavaScript, as app authors receive it.
import { insertWords } from "../../sdk/js/ui.js";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const T = 100_000;

/** The shell's recorder, run by the real state machine, with the microphone and server left out. */
function fakeRecorder(hosted = false) {
  let state: RecorderState = IDLE;
  const listeners = new Set<() => void>();
  const presses: string[] = [];
  const dispatch = (event: RecorderEvent) => {
    const next = step(state, event).state;
    if (next === state) return;
    state = next;
    for (const listener of listeners) listener();
  };
  const recorder: DictateRecorder = {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    press: () => {
      presses.push(state.phase);
      dispatch({ type: "press" });
    },
    cancel: () => dispatch({ type: "cancel" }),
    hosted: () => hosted,
  };
  return {
    recorder,
    presses,
    started: () => dispatch({ type: "started", at: T, maxSeconds: 300, maxBytes: 1_000_000 }),
    transcribed: () => dispatch({ type: "transcribed" }),
    failed: (problem: Problem) => dispatch({ type: "failed", problem }),
    refused: (problem: Problem) => dispatch({ type: "refused", problem }),
    state: () => state,
  };
}

/** Opens one dictation on a desk; `heard` is how the transcription hands over the words. */
function begin(desk = createDesk(), owner: object = {}) {
  const fake = fakeRecorder();
  let heard: (text: string) => void = () => undefined;
  let settled: { ok: true; value: unknown } | { ok: false; error: BridgeError } | null = null;
  const answer = desk.open({ owner, appName: "Journal", hint: "Today", maxSeconds: 300 }, (give) => {
    heard = give;
    return fake.recorder;
  });
  answer.then(
    (value) => (settled = { ok: true, value }),
    (error: BridgeError) => (settled = { ok: false, error }),
  );
  return { desk, owner, fake, answer, heard: (text: string) => heard(text), settled: () => settled };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function status(changes: Partial<SpeechStatus> = {}): SpeechStatus {
  return {
    engine: "local",
    ready: true,
    problem: null,
    hosted: false,
    location: "local",
    own_machines: true,
    model: "base.en",
    warm: false,
    download: null,
    key_set: false,
    hosted_callers: [],
    language: "",
    limits: { max_seconds: 600, max_bytes: 25 * 1024 * 1024, busy_wait_seconds: 5 },
    accepted_types: ["audio/webm"],
    ...changes,
  };
}

const ENV = { secure: true, microphoneApi: true, recorderApi: true };

// ---------------------------------------------------------------- the permission

test("a page without speech.dictate is refused both calls and the shell never starts a recording", async () => {
  const grant: Grant = { tier: "full", permissions: ["model.ask"] };
  const ran: string[] = [];
  const posted: BridgeResponse[] = [];
  const relay = createRelay({
    appId: "journal",
    grant,
    send: async () => assert.fail("a speech call never reaches the server"),
    post: (response) => posted.push(response),
    shell: { "speech.dictate": () => ran.push("dictate"), "speech.available": () => ran.push("available") },
  });
  await relay.handle({ id: 1, call: "speech.dictate", args: {} });
  await relay.handle({ id: 2, call: "speech.available", args: {} });
  assert.deepEqual(ran, []);
  for (const response of posted) {
    assert.equal(response.ok, false);
    if (!response.ok) assert.ok(response.error.message.includes("\"speech.dictate\""), response.error.message);
  }
  assert.deepEqual(CALLS["speech.dictate"], { needs: "speech.dictate", shell: true });
  assert.deepEqual(CALLS["speech.available"], { needs: "speech.dictate", shell: true });
});

// ---------------------------------------------------------------- the owner's touch

test("dictation needs the owner's touch just before, and one touch is one dictation", () => {
  assert.equal(dictateRefusal(null, null, T, 0)?.code, "not_allowed");
  assert.equal(dictateRefusal(T - SAVE_WINDOW_MS - 1, null, T, 0)?.code, "not_allowed");
  assert.equal(dictateRefusal(T + 10, null, T, 0)?.code, "not_allowed");
  assert.equal(dictateRefusal(T - 100, null, T, 0), null);
  const again = dictateRefusal(T - 100, T - 50, T, 0);
  assert.equal(again?.code, "quota");
  assert.equal(again?.message, DICTATE_REFUSALS.tooSoon);
  assert.equal(dictateRefusal(T - 100, T - 200, T, 0), null);
  // Every refusal has a sentence for the owner as well as one for the page.
  assert.ok(dictateRefusal(null, null, T, 0)?.owner_message);
});

// ---------------------------------------------------------------- one at a time

test("a second dictation while one is open is refused with a sentence, from any frame", () => {
  const desk = createDesk();
  const first = begin(desk);
  assert.equal(first.fake.state().phase, "requesting");
  assert.throws(
    () => desk.open({ owner: {}, appName: "Other", hint: "", maxSeconds: 60 }, () => fakeRecorder().recorder),
    (error: BridgeError) => error.code === "quota" && error.message === DICTATE_REFUSALS.busy,
  );
  assert.equal(desk.current()?.appName, "Journal");
});

test("no app may dictate while the shell's own dictate button is recording", () => {
  const set = createOpenSet();
  const button = {};
  set.note(button, { phase: "recording", startedAt: T, stopAt: T + 1000, maxBytes: 10 });
  const desk = createDesk(() => set.count());
  assert.throws(() => desk.open({ owner: {}, appName: "Journal", hint: "", maxSeconds: 60 }, () => fakeRecorder().recorder));
  assert.equal(dictateRefusal(T - 1, null, T, set.count())?.message, DICTATE_REFUSALS.busy);
  set.note(button, IDLE);
  assert.equal(set.count(), 0);
});

test("a recorder counts as open from the press until its recording is done or dropped", () => {
  assert.equal(holdsRecording(IDLE), false);
  assert.equal(holdsRecording({ phase: "requesting" }), true);
  assert.equal(holdsRecording({ phase: "uploading", reason: "pressed" }), true);
  assert.equal(holdsRecording({ phase: "error", problem: { code: "busy", message: "x", retry: true } }), true);
  assert.equal(holdsRecording({ phase: "error", problem: { code: "off", message: "x", retry: false } }), false);
  assert.equal(OPEN_RECORDINGS.count(), 0);
});

// ---------------------------------------------------------------- what the page gets

test("nothing is answered while it records, and the answer is the words and nothing else", async () => {
  const run = begin();
  run.fake.started();
  await tick();
  assert.equal(run.settled(), null);
  assert.equal(run.desk.current()?.state.phase, "recording");
  run.desk.stop();
  assert.deepEqual(run.fake.presses, ["idle", "recording"]);
  assert.equal(run.fake.state().phase, "uploading");
  await tick();
  assert.equal(run.settled(), null);
  run.heard("  buy oat milk  ");
  run.fake.transcribed();
  await tick();
  const settled = run.settled();
  assert.ok(settled && settled.ok);
  if (settled?.ok) assert.deepEqual(settled.value, { text: "buy oat milk" });
  assert.equal(run.desk.current(), null);
});

test("a recording with no speech in it answers empty words, not an error", async () => {
  const run = begin();
  run.fake.started();
  run.desk.stop();
  run.heard("");
  run.fake.transcribed();
  await tick();
  assert.deepEqual(run.settled(), { ok: true, value: { text: "" } });
});

test("Cancel rejects with a code no failure uses, and the recorder lets go", async () => {
  const run = begin();
  run.fake.started();
  run.desk.cancel();
  await tick();
  const settled = run.settled();
  assert.ok(settled && !settled.ok);
  if (settled && !settled.ok) assert.equal(settled.error.code, "cancelled");
  assert.equal(run.fake.state().phase, "idle");
  assert.equal(run.desk.current(), null);
  assert.ok((ERROR_CODES as readonly string[]).includes("cancelled"));
  for (const code of ["off", "denied", "busy", "too_long", "refused", "unsupported"] as const) {
    assert.notEqual(problemError({ code, message: "x", retry: false }, true).code, "cancelled");
  }
  assert.equal(cancelledError().code, "cancelled");
});

test("only the frame that asked can be cancelled by its screen going away", async () => {
  const run = begin();
  run.desk.cancel({});
  assert.notEqual(run.desk.current(), null);
  run.desk.cancel(run.owner);
  await tick();
  assert.equal(run.desk.current(), null);
});

test("a refusal before recording rejects with the sentence that says where to turn dictation on", async () => {
  const run = begin();
  run.fake.refused({ code: "off", message: "Dictation is turned off. Turn it on under Settings, Voice.", retry: false });
  await tick();
  const settled = run.settled();
  assert.ok(settled && !settled.ok);
  if (settled && !settled.ok) {
    assert.equal(settled.error.code, "unavailable");
    assert.ok(settled.error.owner_message?.includes("Settings, Voice"));
    assert.ok(settled.error.message.startsWith("Dictation is not available, so nothing was recorded."));
  }
  assert.equal(run.desk.current(), null);
});

test("a failure that keeps the recording waits for Send again, and one that does not ends it", async () => {
  const run = begin();
  run.fake.started();
  run.desk.stop();
  run.fake.failed({ code: "busy", message: "The model is busy.", retry: true });
  await tick();
  assert.equal(run.settled(), null);
  assert.equal(run.desk.current()?.state.phase, "error");
  run.desk.stop();
  assert.equal(run.fake.state().phase, "uploading");
  run.fake.failed({ code: "refused", message: "That could not be read.", retry: false });
  await tick();
  const settled = run.settled();
  assert.ok(settled && !settled.ok);
  if (settled && !settled.ok) assert.ok(settled.error.message.startsWith("The dictation did not finish"));
  assert.equal(run.fake.state().phase, "idle");
});

test("through the relay the page is posted only {text}, once, and nothing while it records", async () => {
  const desk = createDesk();
  const fake = fakeRecorder();
  let heard: (text: string) => void = () => undefined;
  const posted: BridgeResponse[] = [];
  const relay = createRelay({
    appId: "journal",
    grant: { tier: "full", permissions: ["speech.dictate"] },
    send: async () => assert.fail("never the server"),
    post: (response) => posted.push(response),
    shell: {
      "speech.dictate": () =>
        desk.open({ owner: {}, appName: "Journal", hint: "", maxSeconds: 300 }, (give) => {
          heard = give;
          return fake.recorder;
        }),
    },
  });
  const handled = relay.handle({ id: 7, call: "speech.dictate", args: { hint: "What happened today?" } });
  fake.started();
  await tick();
  assert.deepEqual(posted, []);
  desk.stop();
  heard("a quiet day");
  fake.transcribed();
  await handled;
  assert.deepEqual(posted, [{ id: 7, ok: true, result: { text: "a quiet day" } }]);
});

// ---------------------------------------------------------------- the hint

test("a hostile hint is one short line of plain text, capped", () => {
  const nasty = `<img src=x onerror=alert(1)>${String.fromCharCode(0x202e)}evil${String.fromCharCode(0)}\n\nline${String.fromCharCode(0x2028)}two`;
  const hint = cleanHint(nasty);
  assert.equal(hint, "<img src=x onerror=alert(1)> evil line two");
  const long = cleanHint("word ".repeat(500));
  assert.ok(Array.from(long).length <= DICTATE_HINT_MAX);
  assert.ok(long.endsWith("…"));
  assert.equal(cleanHint(42), "");
  assert.deepEqual(readDictateArgs({}), { ok: true, hint: "" });
  assert.deepEqual(readDictateArgs(undefined), { ok: true, hint: "" });
  assert.deepEqual(readDictateArgs({ hint: "Say it" }), { ok: true, hint: "Say it" });
  for (const bad of [{ hint: 5 }, { hint: ["x"] }, { hint: "x", app: "other" }, { audio: true }, "hint", [1]]) {
    const result = readDictateArgs(bad);
    assert.equal(result.ok, false, JSON.stringify(bad));
    if (!result.ok) assert.equal(result.error.code, "invalid");
  }
});

test("the surface shows the hint as text and is drawn by the shell, never by the page", () => {
  const surface = read("../src/modules/apps/DictateSurface.tsx");
  assert.ok(surface.includes("{mine.hint}"));
  assert.ok(!/dangerouslySetInnerHTML|innerHTML/.test(surface));
  assert.ok(surface.includes("Recording… press Stop when you are done."));
});

// ---------------------------------------------------------------- the frame stays without a microphone

test("the frame's allow attribute is still empty and no page script reaches for a microphone", () => {
  assert.equal(FRAME_ALLOW, "");
  const frame = read("../src/modules/apps/AppFrame.tsx");
  assert.ok(frame.includes("allow={FRAME_ALLOW}"));
  assert.equal((frame.match(/\ballow=/g) ?? []).length, 1);
  assert.ok(!/getUserMedia|MediaRecorder|mediaDevices/.test(frame));
  for (const file of ["../../sdk/js/bridge.js", "../../sdk/js/ui.js"]) {
    assert.ok(!/getUserMedia|MediaRecorder|AudioContext|mediaDevices/.test(read(file)), file);
  }
});

// ---------------------------------------------------------------- available()

test("available says where to turn dictation on when it is off or not ready", () => {
  const off = availability(status({ engine: "off", ready: false, own_machines: null }), { id: "water", private: false }, ENV);
  assert.equal(off.available, false);
  if (!off.available) assert.ok(off.reason.includes("Settings, Voice"));
  const notReady = availability(status({ ready: false, problem: "Install it with `uv sync --extra speech`." }), { id: "water", private: false }, ENV);
  assert.deepEqual(notReady, { available: false, reason: "Install it with uv sync --extra speech." });
  assert.deepEqual(availability(status(), { id: "water", private: false }, ENV), { available: true });
});

test("a private app is refused under any engine not known to be on the owner's machines", () => {
  const journal = { id: "journal", private: true };
  assert.deepEqual(availability(status(), journal, ENV), { available: true });
  assert.deepEqual(availability(status({ engine: "parakeet" }), journal, ENV), { available: true });
  // A speech server the looser rule calls local, but which is not known to be the owner's.
  const lan = status({ engine: "endpoint", hosted: false, location: "local", own_machines: false });
  assert.deepEqual(availability(lan, journal, ENV), { available: false, reason: PRIVATE_NOT_LOCAL });
  // An older server that does not say counts as not known.
  const unsaid = status({ engine: "endpoint", hosted: false, location: "local", own_machines: undefined });
  assert.deepEqual(availability(unsaid, journal, ENV), { available: false, reason: PRIVATE_NOT_LOCAL });
  const hosted = status({ engine: "endpoint", hosted: true, location: "hosted", own_machines: false, hosted_callers: ["app.journal"] });
  assert.deepEqual(availability(hosted, journal, ENV), { available: false, reason: PRIVATE_NOT_LOCAL });
  // The same servers are fine for an app that is not private, under the hosted rule.
  assert.deepEqual(availability(lan, { id: "journal", private: false }, ENV), { available: true });
  assert.deepEqual(availability(hosted, { id: "journal", private: false }, ENV), { available: true });
  assert.equal(privateProblem({ engine: "off", own_machines: null }, true), null);
});

test("the hosted rule names the app, not the shell", () => {
  assert.equal(dictateCaller("water"), "app.water");
  const hosted = status({ engine: "endpoint", hosted: true, location: "hosted", own_machines: false, hosted_callers: ["dictation"] });
  const answer = availability(hosted, { id: "water", private: false }, ENV);
  assert.equal(answer.available, false);
  if (!answer.available) assert.ok(answer.reason.includes("app.water"));
});

test("available says so when this browser cannot record", () => {
  const answer = availability(status(), { id: "water", private: false }, { secure: false, microphoneApi: false, recorderApi: true });
  assert.equal(answer.available, false);
  if (!answer.available) assert.ok(answer.reason.includes("secure page"));
});

// ---------------------------------------------------------------- the page's Dictate button

test("the words go in at the cursor, or over the selection, followed by a space", () => {
  assert.deepEqual(insertWords("", 0, 0, "hello there"), { value: "hello there ", caret: 12 });
  assert.deepEqual(insertWords("Today was", 9, 9, " long "), { value: "Today was long ", caret: 15 });
  assert.deepEqual(insertWords("Today  good", 6, 6, "was"), { value: "Today was  good", caret: 10 });
  assert.deepEqual(insertWords("I felt sad today", 7, 10, "calm"), { value: "I felt calm  today", caret: 12 });
  // No cursor, or one out of range, means the end.
  assert.deepEqual(insertWords("Start", undefined, undefined, "more"), { value: "Start more ", caret: 11 });
  assert.deepEqual(insertWords("Start", 99, -1, "more"), { value: "Start more ", caret: 11 });
  // No words changes nothing.
  assert.deepEqual(insertWords("Kept", 2, 2, "   "), { value: "Kept", caret: 2 });
  // Markup in the words is text in a text box, nothing more.
  assert.equal(insertWords("", 0, 0, "<b>x</b>").value, "<b>x</b> ");
});
