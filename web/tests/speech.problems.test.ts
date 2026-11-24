import assert from "node:assert/strict";
import { test } from "node:test";
import {
  RECORDING_TYPES,
  baseType,
  environmentProblem,
  formatDuration,
  formatElapsed,
  gate,
  hostedBlocked,
  levelFromSamples,
  meterHeights,
  microphoneProblem,
  pickRecordingType,
  noticeAction,
  problemNotice,
  recordingFilename,
  recordingProblem,
  stopNotice,
  uploadProblem,
} from "../src/modules/speech/lib/problems.ts";
import {
  affectsSpeechStatus,
  checkKey,
  commandIn,
  limitsLine,
  privacyLine,
  readiness,
  withCaller,
  withoutTicks,
} from "../src/modules/speech/lib/settings.ts";
import type { SpeechStatus } from "../src/modules/speech/types.ts";

const MB = 1024 * 1024;
const ACCEPTED = ["audio/webm", "audio/mp4", "audio/wav", "audio/mpeg", "audio/ogg", "audio/flac", "audio/m4a"];

function status(overrides: Partial<SpeechStatus> = {}): SpeechStatus {
  return {
    engine: "local",
    ready: true,
    problem: null,
    hosted: false,
    location: "local",
    model: "small",
    warm: false,
    download: null,
    key_set: false,
    hosted_callers: [],
    language: "",
    limits: { max_seconds: 300, max_bytes: 25 * MB, busy_wait_seconds: 20 },
    accepted_types: ACCEPTED,
    ...overrides,
  };
}

const OFF = status({ engine: "off", ready: false, problem: "Speech to text is turned off.", location: null, model: null });
const HOSTED = status({ engine: "endpoint", hosted: true, location: "hosted", model: "sample-model", key_set: true });

test("a ready local engine lets any caller record", () => {
  assert.equal(gate(status(), "dictation"), null);
  assert.equal(gate(status(), "notes"), null);
});

test("dictation switched off says where to turn it on", () => {
  const problem = gate(OFF, "dictation");
  assert.equal(problem?.code, "off");
  assert.match(problem?.message ?? "", /Settings, Voice/);
  assert.equal(problem?.retry, false);
});

test("an engine that is not ready shows the server's own reason", () => {
  const reason = "Speech on this computer needs the speech add-on, which is not installed.";
  assert.deepEqual(gate(status({ ready: false, problem: reason }), "dictation"), { code: "not_ready", message: reason, retry: false });
  assert.equal(gate(status({ ready: false, problem: "Install it with `uv sync --extra speech`." }), "dictation")?.message, "Install it with uv sync --extra speech.");
  assert.match(gate(status({ ready: false, problem: null }), "dictation")?.message ?? "", /Settings, Voice/);
});

test("a hosted engine refuses a caller that is not on the list, before anything is recorded", () => {
  const problem = gate(HOSTED, "dictation");
  assert.equal(problem?.code, "not_allowed");
  assert.match(problem?.message ?? "", /add dictation to 'Allowed to use a hosted speech server' under Settings, Voice/);
  assert.match(problem?.message ?? "", /Nothing was recorded/);
});

test("a hosted engine lets a listed caller through, whatever the case it was typed in", () => {
  const allowed = { ...HOSTED, hosted_callers: ["dictation", "notes"] };
  assert.equal(gate(allowed, "dictation"), null);
  assert.equal(hostedBlocked(allowed, " Notes "), false);
  assert.equal(hostedBlocked(allowed, "tasks"), true);
  assert.equal(hostedBlocked(status({ hosted_callers: [] }), "tasks"), false);
});

test("off and not ready are reported ahead of the hosted rule", () => {
  assert.equal(gate({ ...HOSTED, ready: false, problem: "No key is saved." }, "dictation")?.code, "not_ready");
});

test("an insecure page explains that the microphone needs https or localhost", () => {
  const problem = environmentProblem({ secure: false, microphoneApi: false, recorderApi: true });
  assert.equal(problem?.code, "insecure");
  assert.match(problem?.message ?? "", /https/);
  assert.match(problem?.message ?? "", /localhost/);
});

test("a browser without recording is called unsupported", () => {
  assert.equal(environmentProblem({ secure: true, microphoneApi: false, recorderApi: true })?.code, "unsupported");
  assert.equal(environmentProblem({ secure: true, microphoneApi: true, recorderApi: false })?.code, "unsupported");
  assert.equal(environmentProblem({ secure: true, microphoneApi: true, recorderApi: true }), null);
});

test("the recording container is the first one both the browser and the server take", () => {
  assert.equal(pickRecordingType(() => true, ACCEPTED), "audio/webm;codecs=opus");
  assert.equal(pickRecordingType((type) => type.startsWith("audio/mp4"), ACCEPTED), "audio/mp4;codecs=mp4a.40.2");
  assert.equal(pickRecordingType((type) => type === "audio/mp4", ACCEPTED), "audio/mp4");
  assert.equal(pickRecordingType(() => true, ["audio/mp4", "audio/wav"]), "audio/mp4;codecs=mp4a.40.2");
  assert.equal(pickRecordingType(() => false, ACCEPTED), null);
  assert.equal(pickRecordingType(() => true, ["audio/wav"]), null);
  assert.equal(
    pickRecordingType(() => {
      throw new Error("unknown type");
    }, ACCEPTED),
    null,
  );
  assert.ok(RECORDING_TYPES.every((type) => ["audio/webm", "audio/mp4", "audio/ogg"].includes(baseType(type))));
});

test("the upload is named for its container", () => {
  assert.equal(baseType("Audio/WebM; codecs=opus"), "audio/webm");
  assert.equal(recordingFilename("audio/webm;codecs=opus"), "dictation.webm");
  assert.equal(recordingFilename("audio/mp4"), "dictation.mp4");
  assert.equal(recordingFilename("audio/ogg;codecs=opus"), "dictation.ogg");
});

test("microphone failures are told apart by the browser's error name", () => {
  assert.equal(microphoneProblem("NotAllowedError").code, "denied");
  assert.equal(microphoneProblem("SecurityError").code, "denied");
  assert.equal(microphoneProblem("NotFoundError").code, "no_microphone");
  assert.equal(microphoneProblem("NotReadableError").code, "microphone");
  assert.equal(microphoneProblem("").code, "microphone");
  assert.ok(["NotAllowedError", "NotFoundError", "NotReadableError", "Other"].every((name) => !microphoneProblem(name).retry));
});

test("an empty or oversized recording is not sent", () => {
  assert.equal(recordingProblem(0, MB)?.code, "empty");
  assert.equal(recordingProblem(MB + 1, MB)?.code, "too_large");
  assert.match(recordingProblem(26 * MB, 25 * MB)?.message ?? "", /limit of 25 MB/);
  assert.equal(recordingProblem(MB, MB), null);
});

test("a busy or unreachable server keeps the recording for a retry; a refusal does not", () => {
  assert.deepEqual(uploadProblem(503, "The speech model is busy with another recording. Try again in a moment."), {
    code: "busy",
    message: "The speech model is busy with another recording. Try again in a moment.",
    retry: true,
  });
  assert.equal(uploadProblem(0, "Cannot reach the server.").retry, true);
  assert.equal(uploadProblem(502, "The speech server could not be reached.").retry, true);
  assert.equal(uploadProblem(504, "The speech server took too long.").retry, true);
  assert.equal(uploadProblem(403, "Not allowed.").retry, false);
  assert.equal(uploadProblem(403, "Not allowed.").code, "not_allowed");
  assert.equal(uploadProblem(409, "Speech to text is turned off.").code, "off");
  assert.match(uploadProblem(409, "x").message, /Settings, Voice/);
  assert.equal(uploadProblem(413, "That recording is longer than the limit of 300 seconds.").code, "too_long");
  assert.equal(uploadProblem(413, "That recording is larger than the limit of 25 MB.").code, "too_large");
  assert.equal(uploadProblem(422, "The recording could not be read.").code, "refused");
  assert.equal(uploadProblem(500, "").message, "The recording could not be transcribed.");
});

test("the toast says how to retry only when the recording is kept", () => {
  assert.equal(problemNotice({ code: "denied", message: "Blocked.", retry: false }), "Blocked.");
  assert.match(problemNotice({ code: "unreachable", message: "Gone.", retry: true }), /^Gone\. Your recording is kept: press the button again/);
});

test("a busy speech engine's toast offers Retry, and no other problem's does", () => {
  const busy = uploadProblem(503, "The speech model is busy. Try again in a moment.");
  assert.equal(noticeAction(busy), "Retry");
  assert.match(problemNotice(busy), /busy\. Try again in a moment\. Your recording is kept: press Retry, or the button again, to resend it/);
  // A 503 that the status shows to be "not set up" keeps no recording, so there is nothing to resend.
  assert.equal(noticeAction({ ...busy, retry: false }), null);
  assert.equal(noticeAction(uploadProblem(0, "Cannot reach the server.")), null);
  assert.equal(noticeAction(uploadProblem(403, "Not allowed.")), null);
  assert.equal(noticeAction({ code: "denied", message: "Blocked.", retry: false }), null);
});

test("a recording stopped for the person says why", () => {
  const limits = { max_seconds: 300, max_bytes: 25 * MB };
  assert.match(stopNotice("time", limits) ?? "", /limit of 5 minutes/);
  assert.match(stopNotice("size", limits) ?? "", /25 MB/);
  assert.match(stopNotice("lost", limits) ?? "", /microphone stopped/);
  assert.equal(stopNotice("pressed", limits), null);
  assert.equal(stopNotice("retry", limits), null);
});

test("durations and the clock read naturally", () => {
  assert.equal(formatDuration(300), "5 minutes");
  assert.equal(formatDuration(60), "1 minute");
  assert.equal(formatDuration(90), "90 seconds");
  assert.equal(formatDuration(1), "1 second");
  assert.equal(formatDuration(5400), "1 hour 30 minutes");
  assert.equal(formatDuration(125), "2 minutes 5 seconds");
  assert.equal(formatDuration(0), "0 seconds");
  assert.equal(formatElapsed(0), "0:00");
  assert.equal(formatElapsed(7_900), "0:07");
  assert.equal(formatElapsed(725_000), "12:05");
  assert.equal(formatElapsed(3_723_000), "1:02:03");
  assert.equal(formatElapsed(-5), "0:00");
});

test("the level meter is still for silence and full for loud speech", () => {
  assert.equal(levelFromSamples([]), 0);
  assert.equal(levelFromSamples([128, 128, 128, 128]), 0);
  assert.equal(levelFromSamples([0, 255, 0, 255]), 1);
  const quiet = levelFromSamples([120, 136, 120, 136]);
  assert.ok(quiet > 0.2 && quiet < 0.3);
  assert.deepEqual(meterHeights(0, 4), [0.15, 0.15, 0.15, 0.15]);
  const loud = meterHeights(1, 4);
  assert.equal(loud.length, 4);
  assert.ok((loud[1] ?? 0) > (loud[0] ?? 0) && loud[1] === loud[2] && loud[0] === loud[3]);
  assert.ok(loud.every((height) => height <= 1));
  assert.deepEqual(meterHeights(5, 1), [1]);
});

test("a key is checked the way the server checks it", () => {
  assert.deepEqual(checkKey("  sample-key-123  "), { ok: true, key: "sample-key-123" });
  assert.equal(checkKey("   ").ok, false);
  assert.equal(checkKey("two words").ok, false);
  assert.equal(checkKey("line\nbreak").ok, false);
  assert.equal(checkKey("café").ok, false);
  assert.equal(checkKey("k".repeat(500)).ok, true);
  assert.equal(checkKey("k".repeat(501)).ok, false);
});

test("the install command is taken from the server's message", () => {
  const message = "Speech on this computer needs the speech add-on. Install it with `uv sync --extra speech` and restart JimmyHQ.";
  assert.equal(commandIn(message), "uv sync --extra speech");
  assert.equal(commandIn("No model is chosen."), null);
  assert.equal(commandIn(null), null);
  assert.equal(withoutTicks(message), "Speech on this computer needs the speech add-on. Install it with uv sync --extra speech and restart JimmyHQ.");
});

test("the card says ready, not ready or off", () => {
  assert.deepEqual(readiness(status()), { tone: "ok", label: "Ready" });
  assert.deepEqual(readiness(status({ ready: false })), { tone: "warn", label: "Not ready" });
  assert.deepEqual(readiness(OFF), { tone: "off", label: "Off" });
});

test("the privacy line says where the audio goes", () => {
  assert.equal(privacyLine(OFF), null);
  assert.match(privacyLine(status()) ?? "", /never leaves/);
  assert.match(privacyLine(HOSTED) ?? "", /^Hosted: your audio is sent/);
  assert.match(privacyLine(status({ engine: "endpoint" })) ?? "", /your own network/);
});

test("the limits are written out, with the busy wait only for the local engine", () => {
  assert.equal(
    limitsLine(status()),
    "A recording can be up to 5 minutes long and 25 MB. If the model is busy, a recording waits up to 20 seconds before it is told to try again.",
  );
  assert.equal(limitsLine(HOSTED), "A recording can be up to 5 minutes long and 25 MB.");
});

test("the status is asked for again after a voice setting, the key or a transcription changes", () => {
  assert.equal(affectsSpeechStatus({ kind: "setting.updated", payload: { key: "speech.engine", value: "local" } }), true);
  assert.equal(affectsSpeechStatus({ kind: "setting.reset", payload: { key: "speech.max_seconds" } }), true);
  assert.equal(affectsSpeechStatus({ kind: "setting.updated", payload: { key: "appearance.theme" } }), false);
  assert.equal(affectsSpeechStatus({ kind: "setting.updated", payload: {} }), false);
  assert.equal(affectsSpeechStatus({ kind: "speech.key_changed", payload: { set: true } }), true);
  assert.equal(affectsSpeechStatus({ kind: "speech.transcribed", payload: {} }), true);
  assert.equal(affectsSpeechStatus({ kind: "task.created", payload: {} }), false);
});

test("allowing a caller adds it once", () => {
  assert.deepEqual(withCaller([], "Dictation"), ["dictation"]);
  assert.deepEqual(withCaller(["notes"], "dictation"), ["notes", "dictation"]);
  assert.deepEqual(withCaller(["dictation"], "dictation"), ["dictation"]);
});
