import assert from "node:assert/strict";
import { test } from "node:test";
import { downloadSize, downloadState, limitsLine, privacyLine, runsHere } from "../src/modules/speech/lib/settings.ts";
import type { SpeechDownload, SpeechStatus } from "../src/modules/speech/types.ts";

const MB = 1024 * 1024;

function download(overrides: Partial<SpeechDownload> = {}): SpeechDownload {
  return { present: false, megabytes: 670, host: "huggingface.co", preparing: false, problem: null, ...overrides };
}

function parakeet(overrides: Partial<SpeechStatus> = {}): SpeechStatus {
  return {
    engine: "parakeet",
    ready: true,
    problem: null,
    hosted: false,
    location: "local",
    model: "parakeet-tdt-0.6b-v3 int8",
    warm: false,
    download: download(),
    key_set: false,
    hosted_callers: [],
    language: "",
    limits: { max_seconds: 300, max_bytes: 25 * MB, busy_wait_seconds: 20 },
    accepted_types: ["audio/webm", "audio/mp4", "audio/wav", "audio/ogg"],
    ...overrides,
  };
}

test("parakeet counts as running on this computer, like whisper, and a speech server does not", () => {
  assert.equal(runsHere("parakeet"), true);
  assert.equal(runsHere("local"), true);
  assert.equal(runsHere("endpoint"), false);
  assert.equal(runsHere("off"), false);
  assert.match(privacyLine(parakeet()) ?? "", /never leaves/);
  assert.match(limitsLine(parakeet()), /If the model is busy/);
});

test("a download size reads in megabytes below a gigabyte and in gigabytes above", () => {
  assert.equal(downloadSize(670), "670 MB");
  assert.equal(downloadSize(2000), "2 GB");
  assert.equal(downloadSize(2550), "2.5 GB");
});

test("an engine with nothing to download has no download line", () => {
  assert.equal(downloadState(parakeet({ download: null })), null);
});

test("a model not yet downloaded says how big it is, where from, that audio is not uploaded, and offers the button", () => {
  const state = downloadState(parakeet());
  assert.equal(state?.tone, "warn");
  assert.match(state?.text ?? "", /not downloaded yet/);
  assert.match(state?.text ?? "", /about 670 MB from huggingface\.co/);
  assert.match(state?.text ?? "", /audio is never uploaded/);
  assert.equal(state?.action, "Download the model now");
});

test("a downloaded model that is not loaded offers to load it, and a loaded one offers nothing", () => {
  assert.equal(downloadState(parakeet({ download: download({ present: true }) }))?.action, "Load it now");
  const loaded = downloadState(parakeet({ warm: true, download: download({ present: true }) }));
  assert.equal(loaded?.tone, "ok");
  assert.equal(loaded?.action, null);
});

test("while the download runs the line says so and there is no button to press twice", () => {
  const state = downloadState(parakeet({ download: download({ preparing: true }) }));
  assert.equal(state?.tone, "busy");
  assert.equal(state?.action, null);
  assert.match(state?.text ?? "", /several minutes/);
});

test("a failed download shows the server's reason without backticks and offers to try again", () => {
  const state = downloadState(parakeet({ download: download({ problem: "Check that this computer can reach `huggingface.co`." }) }));
  assert.equal(state?.text, "Check that this computer can reach huggingface.co.");
  assert.equal(state?.action, "Try the download again");
});

test("without the add-on there is no download button: the problem line says how to install it", () => {
  const state = downloadState(parakeet({ ready: false, problem: "Install it with `uv sync --extra speech-parakeet`.", download: download({ present: null }) }));
  assert.equal(state?.action, null);
  assert.match(state?.text ?? "", /cannot be told/);
});
