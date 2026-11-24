// The Packages page: times and the schedule in words.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { carrierLine, eventLine, refreshLabel, refreshWords, removeQuestion } from "../../apps/packages/web/lib/lines.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { ago, checkedLine, nextLine, soon, when } from "../../apps/packages/web/lib/times.js";

const NOW = 1_800_000_000_000;
const MIN = 60_000;
const HOUR = 60 * MIN;

test("how long ago reads in minutes, hours and days", () => {
  assert.equal(ago(NOW - 10_000, NOW), "just now");
  assert.equal(ago(NOW - MIN, NOW), "1 minute ago");
  assert.equal(ago(NOW - 5 * MIN, NOW), "5 minutes ago");
  assert.equal(ago(NOW - 3 * HOUR, NOW), "3 hours ago");
  assert.equal(ago(NOW - 49 * HOUR, NOW), "2 days ago");
  assert.equal(ago(null, NOW), "never");
});

test("how long until reads in minutes, hours and days, and says now when it is time", () => {
  assert.equal(soon(NOW - 1, NOW), "now");
  assert.equal(soon(NOW + 20 * MIN, NOW), "in 20 minutes");
  assert.equal(soon(NOW + 90 * MIN, NOW), "in 2 hours");
  assert.equal(soon(NOW + 50 * HOUR, NOW), "in 3 days");
});

test("a time is shown in the owner's own time zone", () => {
  assert.notEqual(when(NOW, "Pacific/Auckland"), when(NOW, "America/Los_Angeles"));
  assert.equal(when(0, "UTC"), "");
  assert.ok(when(NOW, "Not/AZone").length > 0);  // a bad zone falls back instead of failing
});

test("the checked line says when, or that it was never checked, or that the owner set it", () => {
  assert.equal(checkedLine({ checked_ms: NOW - 2 * HOUR, status_by: "tracker" }, NOW, true), "Checked 2 hours ago");
  assert.equal(checkedLine({ checked_ms: null, status_by: "tracker" }, NOW, true), "Not checked yet");
  assert.equal(checkedLine({ checked_ms: null, status_by: "hand", archived: true }, NOW, true), "Marked by you; no longer checked");
  assert.equal(checkedLine({ status_by: "hand" }, NOW, false), "Set by you");
  assert.equal(checkedLine({ status_by: "tracker" }, NOW, false, true), "Practice: it moves along by itself");
  assert.equal(checkedLine({ status_by: "hand" }, NOW, false, true), "Set by you");
});

test("the schedule line says the last check and the next, and what a failed try means", () => {
  const base = { outside: true, last_ok_ms: NOW - HOUR, next_ms: NOW + 3 * HOUR, fails: 0 };
  assert.equal(nextLine(base, NOW), "Last checked 1 hour ago. Next check in 3 hours.");
  assert.match(nextLine({ ...base, fails: 2, next_ms: NOW + 30 * MIN }, NOW), /last 2 tries got no clear answer; the next is in 30 minutes/);
  assert.match(nextLine({ outside: true, last_ok_ms: null, next_ms: 0, fails: 0 }, NOW), /Not checked yet\. Next check now\./);
  assert.match(nextLine({ outside: false }, NOW), /Practice looks nothing up/);
  const practice = nextLine({ outside: false, practice: true, practice_minutes: 15 }, NOW);
  assert.match(practice, /^This is Practice: nothing is looked up/);
  assert.match(practice, /delivered in about 15 minutes, with a card on your Bench/);
  assert.match(nextLine({ outside: false, practice: true, practice_minutes: 1 }, NOW), /about 1 minute,/);
});

test("the carrier is said as chosen, detected or guessed, by name when the owner saved one", () => {
  assert.equal(carrierLine({ carrier: "3011", carrier_name: "Corner Post" }), "Carrier: Corner Post");
  assert.equal(carrierLine({ carrier: "3011", carrier_name: "" }), "Carrier: code 3011");
  assert.equal(carrierLine({ carrier: "", carrier_found: "3021", carrier_guessed: false, carrier_name: "" }), "Carrier: code 3021 (detected)");
  assert.equal(carrierLine({ carrier: "", carrier_found: "3021", carrier_guessed: true, carrier_name: "" }), "Carrier: code 3021 (a guess by the service)");
  assert.equal(carrierLine({ carrier: "", carrier_found: "" }), "Carrier: detected automatically");
});

test("the last event is shown with its time, or says there is no news yet", () => {
  assert.equal(eventLine({ event: "Arrived at the depot", event_ms: null, status: "on_its_way" }, "UTC"), "Arrived at the depot");
  assert.match(eventLine({ event: "Arrived", event_ms: NOW, status: "on_its_way" }, "UTC"), /^Arrived · /);
  assert.equal(eventLine({ event: "", status: "waiting" }, "UTC"), "No news from the carrier yet.");
  assert.equal(eventLine({ event: "", status: "on_its_way" }, "UTC"), "");
});

test("Refresh now shows a busy state and then says what happened in the right tone", () => {
  assert.equal(refreshLabel(true), "Checking…");
  assert.equal(refreshLabel(false), "Refresh now");
  assert.deepEqual(refreshWords({ ok: true, said: "Checked 2 packages; nothing changed." }), { words: "Checked 2 packages; nothing changed.", tone: "ok" });
  assert.deepEqual(refreshWords({ ok: false, said: "Nothing was changed." }), { words: "Nothing was changed.", tone: "bad" });
  assert.deepEqual(refreshWords(null), { words: "", tone: "" });
});

test("removing asks in the page's own words and names the package", () => {
  const q = removeQuestion({ label: "Garden hose" });
  assert.equal(q.title, "Remove Garden hose?");
  assert.match(q.intro, /for good/);
});
