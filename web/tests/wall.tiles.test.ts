import assert from "node:assert/strict";
import { test } from "node:test";
import type { HealthEntry, UsageWindow } from "../src/modules/models/api.ts";
import type { Task } from "../src/modules/tasks/api.ts";
import {
  benchSummary,
  moreText,
  progressText,
  providerLines,
  providersText,
  staleAfter,
  todaySummary,
  usageRows,
  waitedMs,
} from "../src/modules/wall/lib/tiles.ts";

test("every event changes the Tape's size, and only its own tile besides", () => {
  assert.deepEqual(staleAfter("system.opened"), ["tape"]);
  assert.deepEqual(staleAfter("task.completed"), ["tape", "today"]);
  assert.deepEqual(staleAfter("bench.card.filed"), ["tape", "bench"]);
  assert.deepEqual(staleAfter("model.called"), ["tape", "usage", "models"]);
  assert.deepEqual(staleAfter("model.failed"), ["tape", "usage", "models"]);
  assert.deepEqual(staleAfter("model.prices.set"), ["tape", "usage"]);
  assert.deepEqual(staleAfter("model.provider.saved"), ["tape", "models"]);
});

test("a changed setting is read again, with whatever it governs", () => {
  assert.deepEqual(staleAfter("setting.updated", { key: "wall.tiles" }), ["tape", "settings"]);
  assert.deepEqual(staleAfter("setting.reset", { key: "appearance.theme" }), ["tape", "settings"]);
  assert.deepEqual(staleAfter("setting.updated", { key: "owner.timezone" }), ["tape", "settings", "today"]);
  assert.deepEqual(staleAfter("setting.updated", { key: "tasks.default_area" }), ["tape", "settings", "today"]);
  assert.deepEqual(staleAfter("setting.updated", { key: "models.currency" }), ["tape", "settings", "usage", "models"]);
  assert.deepEqual(staleAfter("setting.updated", { key: 7 }), ["tape", "settings"]);
});

test("the Bench is summed up by kind and by source, most first", () => {
  const summary = benchSummary({
    counts: { needs: 4, done: 1, waiting: 2 },
    needs_by_kind: { "posts.draft": 1, "notes.finding": 2, "mail.reply": 1, "old.kind": 0 },
    needs_by_source: { Posts: 1, Notes: 3 },
    oldest_needs_since: 1_000,
  });
  assert.equal(summary.needs, 4);
  assert.deepEqual(summary.kinds, [
    { name: "notes.finding", count: 2 },
    { name: "mail.reply", count: 1 },
    { name: "posts.draft", count: 1 },
  ]);
  assert.deepEqual(summary.sources, [
    { name: "Notes", count: 3 },
    { name: "Posts", count: 1 },
  ]);
  assert.equal(summary.oldestSince, 1_000);
});

test("an empty Bench has nothing waiting", () => {
  const summary = benchSummary({ counts: { needs: 0, done: 0, waiting: 0 }, needs_by_kind: {}, needs_by_source: {}, oldest_needs_since: null });
  assert.deepEqual(summary, { needs: 0, kinds: [], sources: [], oldestSince: null });
  assert.equal(waitedMs(summary.oldestSince, 5_000), null);
});

test("a wait is never negative, even if this clock is behind the server's", () => {
  assert.equal(waitedMs(10_000, 70_000), 60_000);
  assert.equal(waitedMs(10_000, 4_000), 0);
});

function task(id: string, extra: Partial<Task> = {}): Task {
  return {
    id,
    title: `Task ${id}`,
    notes: "",
    area: "",
    due: "2030-05-08",
    due_time: null,
    repeat: null,
    repeat_text: null,
    assignee: "owner",
    status: "open",
    created: 0,
    updated: 0,
    done_at: null,
    dropped_at: null,
    filed_by: "owner",
    next_due: null,
    due_today: true,
    lapsed_days: 0,
    pushes: 0,
    stalled: false,
    last_kept: null,
    last_kept_on: null,
    kept_count: 0,
    can_undo_round: false,
    ...extra,
  };
}

test("today lists what is still to do first, then what is ticked", () => {
  const doneAt = Date.UTC(2030, 4, 8, 9);
  const summary = todaySummary({
    date: "2030-05-08",
    timezone: "UTC",
    today: [task("a", { lapsed_days: 2, due: "2030-05-06" }), task("b", { due_time: "14:00" })],
    kept_today: [task("c", { repeat: "FREQ=DAILY", lapsed_days: 3 })],
    done: [task("d", { status: "done", done_at: doneAt, due_time: "09:00" }), task("e", { status: "done", done_at: doneAt - 3 * 86_400_000 })],
  });
  assert.deepEqual(summary.lines, [
    { id: "a", title: "Task a", done: false, lapsedDays: 2, time: null },
    { id: "b", title: "Task b", done: false, lapsedDays: 0, time: "14:00" },
    { id: "c", title: "Task c", done: true, lapsedDays: 0, time: null },
    { id: "d", title: "Task d", done: true, lapsedDays: 0, time: null },
  ]);
  assert.equal(summary.done, 2);
  assert.equal(summary.total, 4);
  assert.equal(progressText(summary.done, summary.total), "2 of 4 done");
  assert.equal(moreText(3), "+3 more");
});

function window(extra: Partial<UsageWindow> = {}): UsageWindow {
  return {
    calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    local_calls: 0,
    local_input_tokens: 0,
    local_output_tokens: 0,
    fallback_calls: 0,
    failed_calls: 0,
    estimated_cost: 0,
    unpriced_calls: 0,
    saved_by_local: null,
    models: [],
    ...extra,
  };
}

test("usage sets the last 24 hours beside all time", () => {
  const rows = usageRows(
    {
      currency: "USD",
      last_24h: window({ calls: 12, input_tokens: 900, output_tokens: 300, estimated_cost: 0.12, saved_by_local: 0.1 }),
      all_time: window({ calls: 46_512, input_tokens: 150_000_000, output_tokens: 32_800_000, estimated_cost: 243.49, saved_by_local: 201.12 }),
    },
    "en-US",
  );
  assert.deepEqual(
    rows.map((row) => [row.id, row.day, row.all]),
    [
      ["calls", "12", "46.5K"],
      ["tokens", "1.2K", "182.8M"],
      ["cost", "$0.12", "$243.49"],
      ["saved", "$0.10", "$201.12"],
    ],
  );
});

test("with no savings reference the saving is a dash, not a zero", () => {
  const rows = usageRows({ currency: "USD", last_24h: window(), all_time: window() }, "en-US");
  assert.deepEqual(rows.map((row) => row.all), ["0", "0", "$0.00", "–"]);
});

function provider(id: string, ok: boolean | null, label = `Provider ${id}`): HealthEntry {
  return { id, label, type: "openai_compatible", local: true, ok, checked_ms: ok === null ? null : 1, error: ok === false ? "refused" : null };
}

test("each enabled provider gets a line that says whether it is online", () => {
  const lines = providerLines({ providers: [provider("a", true), provider("b", false), provider("c", null, "")] });
  assert.deepEqual(lines, [
    { id: "a", label: "Provider a", state: "online", text: "online" },
    { id: "b", label: "Provider b", state: "offline", text: "offline" },
    { id: "c", label: "c", state: "unchecked", text: "not checked" },
  ]);
  assert.equal(providersText(lines), "1 of 3 offline");
  assert.equal(providersText(providerLines({ providers: [provider("a", true)] })), "all online");
  assert.equal(providersText(providerLines({ providers: [provider("a", true), provider("c", null)] })), "");
  assert.equal(providersText([]), "");
});
