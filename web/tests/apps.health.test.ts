import assert from "node:assert/strict";
import { test } from "node:test";
import { canRestart, everyText, healthState, healthWords, jobLine, readHealth, restartedToast, startsInLastDay } from "../src/modules/apps/lib/health.ts";
import { readApp } from "../src/modules/apps/lib/wire.ts";

const HOUR = 60 * 60 * 1000;

test("the health answer is read field by field and nothing unexpected is guessed", () => {
  const health = readHealth({
    state: "crashing",
    started_at: 10,
    last_beat: null,
    restarts: [1, "two", 3],
    last_exit: { code: 3, at: 20, ran_s: 0.4 },
    failures: 5,
    log: ["boom", 7, "again"],
    jobs: [{ name: "draft", every_s: 3600, last_run: 5, finished: 6, ok: false, error: "No model.", running: false }, { every_s: 5 }],
  });
  assert.equal(health.state, "crashing");
  assert.deepEqual(health.restarts, [1, 3]);
  assert.deepEqual(health.lastExit, { code: 3, at: 20 });
  assert.deepEqual(health.log, ["boom", "again"]);
  assert.equal(health.jobs.length, 1);
  assert.equal(readHealth({ state: "on fire" }).state, "not_running");
  assert.equal(readHealth(null).jobs.length, 0);
});

test("a program held back after crashing says so, that its data is untouched, and that Restart starts it", () => {
  const words = healthWords("crashing");
  assert.equal(words.label, "Held back after crashing");
  assert.equal(words.tone, "bad");
  assert.match(words.sentence, /data is untouched/);
  assert.match(words.sentence, /Press Restart/);
});

test("each state the server can send has plain words of its own", () => {
  for (const state of ["running", "starting", "restarting", "not_responding", "crashing", "stopped", "not_running"] as const) {
    const words = healthWords(state);
    assert.ok(words.label.length > 0 && words.sentence.endsWith("."), state);
  }
  assert.equal(healthWords("not_responding").label, "Not responding");
  assert.equal(healthWords("stopped").label, "Stopped");
});

test("restart is offered for a full app that is switched on, and for nothing else", () => {
  assert.equal(canRestart({ tier: "full", enabled: true }), true);
  assert.equal(canRestart({ tier: "full", enabled: false }), false);
  assert.equal(canRestart({ tier: "sandboxed", enabled: true }), false);
});

test("the toast after restart says when an app held back is being started again", () => {
  assert.match(restartedToast("Drafts", "crashing"), /held back/);
  assert.equal(restartedToast("Drafts", "running"), "Restarting Drafts.");
});

test("starts in the last day count only the last twenty-four hours", () => {
  const now = 100 * HOUR;
  assert.equal(startsInLastDay([now - 25 * HOUR, now - 23 * HOUR, now - HOUR, now + HOUR], now), 2);
});

test("how often a job runs is said in whole units", () => {
  assert.equal(everyText(60), "every minute");
  assert.equal(everyText(300), "every 5 minutes");
  assert.equal(everyText(3600), "every hour");
  assert.equal(everyText(86400 * 2), "every 2 days");
  assert.equal(everyText(90), "every 90 seconds");
});

test("a job's last run says when, and in the app's words why it did not work", () => {
  const when = (ms: number) => `at ${ms}`;
  const base = { name: "draft", everyS: 3600, lastRun: null, finished: null, ok: null, error: null, running: false };
  assert.equal(jobLine(base, when), "Has not run yet (every hour).");
  assert.equal(jobLine({ ...base, running: true }, when), "Running now (every hour).");
  assert.equal(jobLine({ ...base, lastRun: 5, finished: 6, ok: true }, when), "Last ran at 6 (every hour).");
  assert.match(jobLine({ ...base, lastRun: 5, finished: 6, ok: false, error: "No model." }, when), /did not work: No model\./);
});

test("the list of apps reads every health state the server sends and drops an unknown one", () => {
  assert.equal(healthState("not_responding"), "not_responding");
  assert.equal(healthState("ok"), null);
  assert.equal(readApp({ id: "drafts", tier: "full", health: "crashing" }).health, "crashing");
  assert.equal(readApp({ id: "drafts", tier: "full", health: "<b>" }).health, null);
});
