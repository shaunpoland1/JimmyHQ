import assert from "node:assert/strict";
import { test } from "node:test";
import type { BackupDestination, BackupStatus } from "../src/modules/backup/api.ts";
import { affectsBackupStatus, ago, destinationLines, formatSize, headline, runningLine } from "../src/modules/backup/lib/status.ts";

const NOW = 1_800_000_000_000;
const HOUR = 3_600_000;

function destination(over: Partial<BackupDestination> = {}): BackupDestination {
  return { which: "first", set: true, last_backup: null, last_failure: null, last_test: null, problem: null, next_ms: null, ...over };
}

function status(over: Partial<BackupStatus> = {}): BackupStatus {
  return { on: true, running: false, running_since: null, destinations: [destination()], problem: null, warn_after_days: 3, now: NOW, ...over };
}

test("a time is said as how long ago, in whole units", () => {
  assert.equal(ago(NOW - 10_000, NOW), "just now");
  assert.equal(ago(NOW - 60_000, NOW), "1 minute ago");
  assert.equal(ago(NOW - 3 * HOUR, NOW), "3 hours ago");
  assert.equal(ago(NOW - 49 * HOUR, NOW), "2 days ago");
  assert.equal(ago(NOW + 5_000, NOW), "just now");
});

test("a size is said in the largest unit that keeps it readable", () => {
  assert.equal(formatSize(512), "512 bytes");
  assert.equal(formatSize(1536), "1.5 KB");
  assert.equal(formatSize(50 * 1024 * 1024), "50 MB");
});

test("a folder with no folder chosen says so and is shown as off", () => {
  assert.deepEqual(destinationLines(destination({ set: false }), NOW), { tone: "off", lines: ["No folder chosen."] });
});

test("a folder shows its last backup and its test, and a newer failure beside it", () => {
  const made = { ts: NOW - 2 * HOUR, name: "jimmyhq-backup-x", files: 12, bytes: 2048, duration_ms: 5, pruned: 0 };
  const failure = { ts: NOW - HOUR, reason: "The backup folder is not there." };
  const shown = destinationLines(destination({ last_backup: made, last_failure: failure, last_test: { ts: NOW - HOUR, name: "jimmyhq-backup-x", passed: true, problems: [], duration_ms: 3 } }), NOW);
  assert.deepEqual(shown.lines, [
    "Last backup 2 hours ago: 12 files, 2.0 KB.",
    "The last try 1 hour ago failed: The backup folder is not there.",
    "Restore test passed 1 hour ago.",
  ]);
  assert.equal(shown.tone, "ok");
});

test("an older failure is not shown once a backup has worked since", () => {
  const made = { ts: NOW - HOUR, name: "jimmyhq-backup-x", files: 1, bytes: 1, duration_ms: 1, pruned: 0 };
  const shown = destinationLines(destination({ last_backup: made, last_failure: { ts: NOW - 2 * HOUR, reason: "Old." } }), NOW);
  assert.ok(!shown.lines.some((line) => line.includes("Old.")));
});

test("a folder with a standing problem is shown as needing a look", () => {
  assert.equal(destinationLines(destination({ problem: "The newest backup is 4 days old." }), NOW).tone, "warn");
  assert.deepEqual(headline(status({ problem: "x" })), { tone: "warn", label: "Needs a look" });
  assert.deepEqual(headline(status({ on: false })), { tone: "off", label: "Off" });
  assert.deepEqual(headline(status({ running: true })), { tone: "ok", label: "Working…" });
});

test("a backup event or a change to a backup setting makes the status worth asking for again", () => {
  assert.equal(affectsBackupStatus({ kind: "backup.made" }), true);
  assert.equal(affectsBackupStatus({ kind: "setting.updated", payload: { key: "backup.destination" } }), true);
  assert.equal(affectsBackupStatus({ kind: "setting.updated", payload: { key: "lookup.enabled" } }), false);
  assert.equal(affectsBackupStatus({ kind: "chat.message.sent" }), false);
});

test("nothing is said about a run when none is in progress", () => {
  assert.equal(runningLine(status({ running_since: NOW - HOUR }), { stuckAfterHours: 12, timeZone: "UTC", locale: "en-US" }), null);
});

test("a run in progress is said to have begun at a time in the owner's time zone", () => {
  const since = Date.UTC(2027, 0, 15, 8, 30);
  const now = since + 2 * HOUR;
  const shown = runningLine(status({ running: true, running_since: since, now }), { stuckAfterHours: 12, timeZone: "America/New_York", locale: "en-US" });
  assert.deepEqual(shown, { stuck: false, text: "A backup has been running since 3:30 AM (2 hours ago)." });
});

test("a run that began on an earlier day names the day as well", () => {
  const since = Date.UTC(2027, 0, 15, 8, 30);
  const now = since + 30 * HOUR;
  const shown = runningLine(status({ running: true, running_since: since, now }), { stuckAfterHours: 48, timeZone: "UTC", locale: "en-US" });
  assert.equal(shown?.text, "A backup has been running since Fri, Jan 15, 8:30 AM (1 day ago).");
});

test("a run longer than the owner's limit is called possibly stuck and points at the Bench card", () => {
  const since = Date.UTC(2027, 0, 15, 8, 30);
  const now = since + 13 * HOUR;
  const shown = runningLine(status({ running: true, running_since: since, now }), { stuckAfterHours: 12, timeZone: "UTC", locale: "en-US" });
  assert.equal(shown?.stuck, true);
  assert.match(shown?.text ?? "", /^A backup has been running since 8:30 AM \(13 hours ago\), longer than the 12 hours set for Backups\. It may be stuck\. The card on the Bench/);
  const exactly = runningLine(status({ running: true, running_since: since, now: since + 12 * HOUR }), { stuckAfterHours: 12, timeZone: "UTC", locale: "en-US" });
  assert.equal(exactly?.stuck, false);
});

test("a run with no known start, or an unknown limit or time zone, still gets a sensible line", () => {
  assert.deepEqual(runningLine(status({ running: true }), { stuckAfterHours: 12 }), { stuck: false, text: "A backup is running." });
  const since = Date.UTC(2027, 0, 15, 8, 30);
  const shown = runningLine(status({ running: true, running_since: since, now: since + 90 * 24 * HOUR }), { stuckAfterHours: null, timeZone: "Not/AZone", locale: "en-US" });
  assert.equal(shown?.stuck, false);
  assert.match(shown?.text ?? "", /^A backup has been running since /);
});
