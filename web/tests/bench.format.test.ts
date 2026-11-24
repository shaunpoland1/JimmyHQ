import assert from "node:assert/strict";
import { test } from "node:test";
import {
  dayLabel,
  describeFiler,
  describeOutcome,
  formatAge,
  formatAgo,
  formatWhen,
  fromLocalInput,
  historyLine,
  kindTone,
  reasonNeeded,
  offeredSnoozes,
  snoozeProblem,
  toLocalInput,
  toneOf,
} from "../src/modules/bench/lib/format.ts";

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
// 2030-05-08 is a Wednesday.
const at = (day: number, hour: number, minute = 0) => new Date(2030, 4, day, hour, minute).getTime();

test("ages are compact and never negative", () => {
  assert.equal(formatAge(-5), "now");
  assert.equal(formatAge(59_000), "now");
  assert.equal(formatAge(14 * MIN), "14 m");
  assert.equal(formatAge(6 * HOUR + 59 * MIN), "6 h");
  assert.equal(formatAge(DAY), "1 d");
  assert.equal(formatAge(3.9 * DAY), "3 d");
  assert.equal(formatAgo(10), "just now");
  assert.equal(formatAgo(2 * HOUR), "2 h ago");
});

test("day headings say Today and Yesterday, then the date", () => {
  const now = at(8, 10);
  assert.equal(dayLabel(at(8, 0), now, "en-US"), "Today");
  assert.equal(dayLabel(at(7, 23, 59), now, "en-US"), "Yesterday");
  assert.equal(dayLabel(at(5, 12), now, "en-US"), "Sun, May 5");
  assert.equal(dayLabel(new Date(2029, 11, 31, 12).getTime(), now, "en-US"), "Mon, Dec 31, 2029");
});

test("a nearby moment is named by its day", () => {
  const now = at(8, 10);
  assert.equal(formatWhen(at(8, 16), now, "en-US"), "4:00 PM");
  assert.equal(formatWhen(at(9, 9), now, "en-US"), "tomorrow 9:00 AM");
  assert.equal(formatWhen(at(13, 9), now, "en-US"), "Mon 9:00 AM");
  assert.equal(formatWhen(at(28, 9), now, "en-US"), "May 28, 9:00 AM");
});

test("the filer is read from the server's actor string", () => {
  assert.deepEqual(describeFiler("owner"), { kind: "owner", name: "You", letter: "Y", tone: null });
  const agent = describeFiler("agent:scout");
  assert.equal(agent.kind, "agent");
  assert.equal(agent.name, "scout");
  assert.equal(agent.letter, "S");
  assert.equal(agent.tone, toneOf("scout"));
  assert.deepEqual(describeFiler("token:importer"), { kind: "token", name: "importer", letter: "I", tone: null });
  assert.deepEqual(describeFiler("app"), { kind: "other", name: "app", letter: "A", tone: null });
  assert.equal(describeFiler("agent:").name, "agent:");
});

test("an agent always gets the same one of four colours", () => {
  for (const name of ["scout", "clerk", "a", ""]) {
    const tone = toneOf(name);
    assert.ok(tone >= 0 && tone <= 3);
    assert.equal(toneOf(name), tone);
  }
});

test("the kind pill is green for done work and amber for a finding", () => {
  assert.equal(kindTone("records.filed", "done"), "ok");
  assert.equal(kindTone("notes.finding", "needs"), "warn");
  assert.equal(kindTone("posts.draft", "needs"), "accent");
});

test("the quick snooze choices are the server's, minus any whose time has passed", () => {
  const now = at(8, 10, 20);
  const choices = [
    { id: "later" as const, label: "Later today", until: at(8, 14) },
    { id: "tomorrow" as const, label: "Tomorrow morning", until: at(9, 6) },
    { id: "week" as const, label: "Next week", until: at(12, 6) },
  ];
  // Shown as sent: a 6:00 morning and a Sunday week start are the owner's settings, not ours to redo.
  assert.deepEqual(offeredSnoozes(choices, now), choices);
  // The Bench was read a while ago and "later today" has come and gone.
  assert.deepEqual(offeredSnoozes(choices, at(8, 14)).map((choice) => choice.id), ["tomorrow", "week"]);
  assert.deepEqual(offeredSnoozes(undefined, now), []);
});

test("a snooze time is worded in the owner's time zone when one is named", () => {
  const now = Date.UTC(2030, 4, 8, 22); // Wednesday 22:00 UTC: already Thursday morning in Tokyo
  const until = Date.UTC(2030, 4, 9, 0); // 09:00 on Thursday in Tokyo
  assert.equal(formatWhen(until, now, "en-US", "Asia/Tokyo").replace(/\s/g, " "), "9:00 AM");
  assert.equal(formatWhen(until + 24 * HOUR, now, "en-US", "Asia/Tokyo").replace(/\s/g, " "), "tomorrow 9:00 AM");
  assert.equal(formatWhen(until, now, "en-US", "Pacific/Honolulu").replace(/\s/g, " "), "2:00 PM");
  // A zone this device does not know, or "local", falls back to local time without failing.
  assert.equal(formatWhen(until, now, "en-US", "Nowhere/Nothing"), formatWhen(until, now, "en-US"));
  assert.equal(formatWhen(until, now, "en-US", "local"), formatWhen(until, now, "en-US"));
});

test("a picked time round-trips through the date and time field", () => {
  const ts = at(9, 7, 5);
  assert.equal(toLocalInput(ts), "2030-05-09T07:05");
  assert.equal(fromLocalInput("2030-05-09T07:05"), ts);
  assert.equal(fromLocalInput(""), null);
  assert.equal(fromLocalInput("soon"), null);
});

test("a snooze time must be in the future and within a year", () => {
  const now = at(8, 10);
  assert.equal(snoozeProblem(null, now), "Pick a date and a time.");
  assert.equal(snoozeProblem(now, now), "A card can only be snoozed until a time in the future.");
  assert.equal(snoozeProblem(now + 400 * DAY, now), "A card cannot be snoozed for more than a year.");
  assert.equal(snoozeProblem(now + HOUR, now), null);
});

test("the missing-reason sentence matches the server's", () => {
  assert.equal(reasonNeeded("No"), "A reason is needed for 'No'. Say why in a sentence and try again.");
});

test("the toast says what the decision did", () => {
  assert.equal(describeOutcome({ label: "Approve", closes: true, moves_to: null }, "Draft ready"), "Approve: “Draft ready” is closed.");
  assert.equal(describeOutcome({ label: "Not yet", closes: false, moves_to: "waiting" }, "Draft ready"), "Not yet: “Draft ready” moved to Waiting.");
  assert.equal(describeOutcome({ label: "Nudge", closes: false, moves_to: null }, "Draft ready"), "Nudge: recorded on “Draft ready”.");
  const long = describeOutcome({ label: "Approve", closes: true, moves_to: null }, "x".repeat(200));
  assert.ok(long.length < 90);
  assert.ok(long.includes("…"));
});

test("history entries read as sentences with notes and reasons underneath", () => {
  const now = at(8, 10);
  assert.deepEqual(historyLine({ type: "filed", ts: now, actor: "agent:scout" }, now), { text: "scout filed this", extra: [] });
  assert.deepEqual(
    historyLine({ type: "updated", ts: now, actor: "agent:scout", note: "Shortened it.", revised: ["title", "body"] }, now),
    { text: "scout revised the title, body", extra: ["Shortened it."] },
  );
  assert.deepEqual(historyLine({ type: "updated", ts: now, actor: "owner", note: "Hello", revised: [] }, now), {
    text: "You added a note",
    extra: ["Hello"],
  });
  assert.deepEqual(
    historyLine(
      { type: "decided", ts: now, actor: "owner", action: "no", label: "No", note: "Later", reason: "Too long", closes: true, moves_to: null },
      now,
    ),
    { text: "You chose No (closed)", extra: ["Reason: Too long", "Note: Later"] },
  );
  assert.equal(
    historyLine({ type: "decided", ts: now, actor: "owner", action: "not_yet", label: "Not yet", note: null, reason: null, closes: false, moves_to: "waiting" }, now).text,
    "You chose Not yet (moved to Waiting)",
  );
  assert.equal(historyLine({ type: "snoozed", ts: now, actor: "owner", until: at(9, 9) }, now, "en-US").text, "You snoozed it until tomorrow 9:00 AM");
  assert.equal(historyLine({ type: "unsnoozed", ts: now, actor: "owner" }, now).text, "You unsnoozed it");
  assert.deepEqual(historyLine({ type: "withdrawn", ts: now, actor: "agent:scout", note: null }, now), { text: "scout withdrew this", extra: [] });
});
