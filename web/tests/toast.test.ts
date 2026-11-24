// How long a toast stays, and the countdown that waits while the owner is on it.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DEFAULT_ACTION_SECONDS,
  RESUME_FLOOR_MS,
  actionSeconds,
  leftAt,
  lifetimeMs,
  pauseCountdown,
  resumeCountdown,
  startCountdown,
} from "../src/lib/toast.ts";

test("a toast with something to press stays for the owner's setting, and at least 20 seconds by default", () => {
  assert.ok(DEFAULT_ACTION_SECONDS >= 20);
  assert.equal(lifetimeMs("success", true), DEFAULT_ACTION_SECONDS * 1000);
  assert.equal(lifetimeMs("success", true, 45), 45_000);
  assert.equal(lifetimeMs("error", true, 45), 45_000);
});

test("a toast with a button never stays for less time than the same toast without one", () => {
  for (const kind of ["info", "success", "error"] as const) {
    assert.ok(lifetimeMs(kind, true, 1) >= lifetimeMs(kind, false, 1), kind);
  }
});

test("a toast with nothing to press keeps its short time whatever the setting says", () => {
  assert.equal(lifetimeMs("info", false, 300), 4000);
  assert.equal(lifetimeMs("error", false, 300), 8000);
});

test("a setting value that is not a usable number gives the default", () => {
  assert.equal(actionSeconds(30), 30);
  for (const value of [undefined, null, "30", 0, -5, Number.NaN, Number.POSITIVE_INFINITY, true]) {
    assert.equal(actionSeconds(value), DEFAULT_ACTION_SECONDS);
  }
});

test("a running countdown loses the time that passed and never goes below nothing", () => {
  const countdown = startCountdown(20_000, 1000);
  assert.equal(leftAt(countdown, 1000), 20_000);
  assert.equal(leftAt(countdown, 6000), 15_000);
  assert.equal(leftAt(countdown, 99_000), 0);
  // A clock that steps back takes nothing away and adds nothing.
  assert.equal(leftAt(countdown, 500), 20_000);
});

test("a countdown waits for as long as the pointer or the keyboard is on a toast", () => {
  const paused = pauseCountdown(startCountdown(20_000, 0), 5000);
  assert.deepEqual(paused, { leftMs: 15_000, since: null });
  assert.equal(leftAt(paused, 5000 + 60 * 60 * 1000), 15_000);
  // Pausing twice changes nothing.
  assert.deepEqual(pauseCountdown(paused, 9000), paused);
  const resumed = resumeCountdown(paused, 70_000);
  assert.deepEqual(resumed, { leftMs: 15_000, since: 70_000 });
  assert.equal(leftAt(resumed, 80_000), 5000);
});

test("a toast shown while the owner is on another starts out waiting", () => {
  const countdown = startCountdown(20_000, 1000, true);
  assert.equal(countdown.since, null);
  assert.equal(leftAt(countdown, 500_000), 20_000);
});

test("a toast about to go still stays a moment after the pointer leaves it", () => {
  const nearlyGone = pauseCountdown(startCountdown(4000, 0), 3990);
  assert.equal(nearlyGone.leftMs, 10);
  assert.equal(resumeCountdown(nearlyGone, 10_000).leftMs, RESUME_FLOOR_MS);
  // Resuming one that is already running does not add time.
  const running = startCountdown(4000, 0);
  assert.equal(resumeCountdown(running, 3990), running);
});
