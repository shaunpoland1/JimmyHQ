import assert from "node:assert/strict";
import { test } from "node:test";
import type { Task, TaskBoard } from "../src/modules/tasks/api.ts";
import { boardPath } from "../src/modules/tasks/api.ts";
import {
  addDays,
  dayOfTimestamp,
  dayToUtc,
  daysBetween,
  earliestPush,
  formatAgo,
  formatClock,
  formatDay,
  formatLongDay,
  lateText,
  pushOneDayTarget,
} from "../src/modules/tasks/lib/dates.ts";
import { changesBody, createBody, formFor, hasChanges, validateForm } from "../src/modules/tasks/lib/form.ts";
import { OWNER_FILTER, agentTasksHidden, assigneesOn, boardColumns, narrowsBeyondDefault, withAgentsDone, doneOn, finishedRangeText, groupOf, groupOpen, planCounts, roundKeptToday, searchTasks, tileFor, todayList } from "../src/modules/tasks/lib/group.ts";
import { listAction, selectionAfterRemoval, stepSelection } from "../src/modules/tasks/lib/keys.ts";
import { applyDone, applyDrop, applyPush, applyRank, applyReopen, applyRestore, applyUndoRound, dropTarget, stepTarget } from "../src/modules/tasks/lib/optimistic.ts";
import { offerUndo, undoLatest, UNDO_LIFETIME_MS } from "../src/modules/tasks/lib/undo.ts";
import { choiceFor, emptyRepeat, ruleFor, toggleDay, weekdayName, weekdaysFrom } from "../src/modules/tasks/lib/repeat.ts";

const TODAY = "2030-01-15";

function task(id: string, over: Partial<Task> = {}): Task {
  return {
    id,
    title: `Task ${id}`,
    notes: "",
    area: "",
    due: null,
    due_time: null,
    repeat: null,
    repeat_text: null,
    assignee: "owner",
    status: "open",
    created: 1,
    updated: 1,
    done_at: null,
    dropped_at: null,
    filed_by: "owner",
    next_due: over.due ?? null,
    due_today: over.due === TODAY,
    lapsed_days: 0,
    pushes: 0,
    stalled: false,
    last_kept: null,
    last_kept_on: null,
    kept_count: 0,
    can_undo_round: false,
    ...over,
  };
}

function board(open: Task[], over: Partial<TaskBoard> = {}): TaskBoard {
  return {
    date: TODAY,
    timezone: "UTC",
    stalled_after: 3,
    done_days: 7,
    open,
    today: open.filter((item) => item.due_today || item.lapsed_days > 0),
    kept_today: [],
    done: [],
    dropped: [],
    areas: ["home", "work"],
    default_area: "",
    counts: { open: open.length, due_today: 0, lapsed: 0, stalled: 0, done_today: 0 },
    ...over,
  };
}

// ---- dates ----

test("a calendar date is only read when it is a real day", () => {
  assert.equal(dayToUtc("2030-02-30"), null);
  assert.equal(dayToUtc("2030-2-3"), null);
  assert.equal(dayToUtc("2030-01-15"), Date.UTC(2030, 0, 15));
});

test("adding days crosses months, years and leap days", () => {
  assert.equal(addDays("2030-01-31", 1), "2030-02-01");
  assert.equal(addDays("2029-12-31", 1), "2030-01-01");
  assert.equal(addDays("2028-02-28", 1), "2028-02-29");
  assert.equal(addDays("2030-03-01", -1), "2030-02-28");
  assert.equal(daysBetween("2030-01-15", "2030-01-22"), 7);
  assert.equal(daysBetween("2030-01-15", "2030-01-10"), -5);
});

test("a one-day push never lands on today or earlier", () => {
  assert.equal(pushOneDayTarget(null, TODAY), "2030-01-16");
  assert.equal(pushOneDayTarget("2030-01-10", TODAY), "2030-01-16");
  assert.equal(pushOneDayTarget(TODAY, TODAY), "2030-01-16");
  assert.equal(pushOneDayTarget("2030-01-20", TODAY), "2030-01-21");
});

test("the earliest push date is later than the due date and not before today", () => {
  assert.equal(earliestPush(null, TODAY), TODAY);
  assert.equal(earliestPush("2030-01-10", TODAY), TODAY);
  assert.equal(earliestPush(TODAY, TODAY), "2030-01-16");
  assert.equal(earliestPush("2030-01-20", TODAY), "2030-01-21");
});

test("a timestamp is placed on the owner's calendar day", () => {
  const lateEvening = Date.UTC(2030, 0, 15, 23, 30);
  assert.equal(dayOfTimestamp(lateEvening, "UTC"), "2030-01-15");
  assert.equal(dayOfTimestamp(lateEvening, "Pacific/Auckland"), "2030-01-16");
  // The browser's own zone is never a stand-in: an unnamed zone has no day here.
  assert.equal(dayOfTimestamp(lateEvening, "local"), null);
  assert.equal(dayOfTimestamp(lateEvening, "Not/AZone"), null);
});

test("dates and times are written in the reader's locale", () => {
  assert.equal(formatDay("2030-01-18", TODAY, "en-US"), "Jan 18");
  assert.equal(formatDay("2031-01-18", TODAY, "en-US"), "Jan 18, 2031");
  assert.equal(formatLongDay("2030-01-15", "en-US"), "Tuesday, Jan 15");
  assert.equal(formatClock("14:00", "en-GB"), "14:00");
  assert.match(formatClock("14:00", "en-US"), /^2:00\sPM$/);
  assert.equal(formatClock("nonsense", "en-US"), "nonsense");
  assert.equal(lateText(1), "1 day late");
  assert.equal(lateText(4), "4 days late");
});

test("how long ago is short for today and a date after that", () => {
  const now = Date.UTC(2030, 0, 15, 12, 0);
  assert.equal(formatAgo(now - 20_000, now), "just now");
  assert.equal(formatAgo(now - 14 * 60_000, now), "14 min ago");
  assert.equal(formatAgo(now - 3 * 3_600_000, now), "3 h ago");
  assert.match(formatAgo(now - 5 * 86_400_000, now, "en-US"), /Jan 10/);
});

// ---- grouping ----

test("open tasks fall into today, tomorrow, this week, later and no date", () => {
  assert.equal(groupOf({ due: "2030-01-10" }, TODAY), "today");
  assert.equal(groupOf({ due: TODAY }, TODAY), "today");
  assert.equal(groupOf({ due: "2030-01-16" }, TODAY), "tomorrow");
  assert.equal(groupOf({ due: "2030-01-17" }, TODAY), "week");
  assert.equal(groupOf({ due: "2030-01-22" }, TODAY), "week");
  assert.equal(groupOf({ due: "2030-01-23" }, TODAY), "later");
  assert.equal(groupOf({ due: null }, TODAY), "none");
});

test("grouping keeps the manual order and leaves out empty groups", () => {
  const open = [task("c", { due: TODAY }), task("a"), task("b", { due: "2030-01-10" }), task("d", { due: "2030-03-01" })];
  const groups = groupOpen(open, TODAY);
  assert.deepEqual(
    groups.map((group) => [group.id, group.tasks.map((item) => item.id)]),
    [
      ["today", ["c", "b"]],
      ["later", ["d"]],
      ["none", ["a"]],
    ],
  );
});

test("search matches every word against the title and the area", () => {
  const open = [task("a", { title: "Water the plants", area: "home" }), task("b", { title: "Plan the report", area: "work" })];
  assert.deepEqual(searchTasks(open, "  ").map((item) => item.id), ["a", "b"]);
  assert.deepEqual(searchTasks(open, "PLAN").map((item) => item.id), ["a", "b"]);
  assert.deepEqual(searchTasks(open, "plan work").map((item) => item.id), ["b"]);
  assert.deepEqual(searchTasks(open, "home").map((item) => item.id), ["a"]);
  assert.deepEqual(searchTasks(open, "nothing"), []);
});

test("the board has now, next, later and work an agent finished", () => {
  const open = [task("a", { due: "2030-01-12" }), task("b", { due: "2030-01-18" }), task("c"), task("d", { due: "2030-02-20" })];
  const done = [task("e", { status: "done", assignee: "agent:scout" }), task("f", { status: "done" })];
  const columns = boardColumns(board(open, { done }));
  assert.deepEqual(
    columns.map((column) => [column.id, column.tasks.map((item) => item.id)]),
    [
      ["now", ["a"]],
      ["next", ["b"]],
      ["later", ["c", "d"]],
      ["done_for_you", ["e"]],
    ],
  );
  assert.deepEqual(planCounts(board(open)), { now: 1, next: 1, later: 2 });
});

test("the today checklist lists what is due, then what was kept or finished today", () => {
  const due = task("a", { due: TODAY });
  const kept = task("b", { repeat: "FREQ=DAILY", last_kept_on: TODAY, due: "2030-01-16" });
  const doneToday = task("c", { status: "done", done_at: Date.UTC(2030, 0, 15, 9) });
  const doneBefore = task("d", { status: "done", done_at: Date.UTC(2030, 0, 13, 9) });
  const list = todayList(board([due, kept], { kept_today: [kept], done: [doneToday, doneBefore] }));
  assert.deepEqual(
    list.items.map((item) => [item.task.id, item.state]),
    [
      ["a", "due"],
      ["b", "kept"],
      ["c", "done"],
    ],
  );
  assert.equal(list.done, 2);
  assert.equal(list.total, 3);
});

test("a task finished today is ticked on the server's word, whatever zone the browser is in", () => {
  // The server's zone is unnamed ("local") and far from this one: done_at alone would
  // put the task on another day (or, read in the browser's zone, on any day at all).
  const farAway = Date.UTC(2030, 0, 16, 13);
  const doneToday = task("a", { status: "done", done_at: farAway, done_on: TODAY });
  const doneYesterday = task("b", { status: "done", done_at: Date.UTC(2030, 0, 15, 9), done_on: "2030-01-14" });
  const noDay = task("c", { status: "done", done_at: Date.UTC(2030, 0, 15, 9) });
  for (const timezone of ["local", "UTC", "Pacific/Auckland"]) {
    const list = todayList(board([], { timezone, done: [doneToday, doneYesterday] }));
    assert.deepEqual(list.items.map((item) => [item.task.id, item.state]), [["a", "done"]]);
    assert.equal(list.done, 1);
  }
  // A task that came without its day is never placed by guessing the server's zone.
  assert.equal(doneOn(noDay, "local"), null);
  assert.equal(doneOn(noDay, "UTC"), TODAY);
  assert.equal(todayList(board([], { timezone: "local", done: [noDay] })).total, 0);
});

test("the finished view says how many days it covers", () => {
  assert.equal(finishedRangeText(7), "Finished or dropped in the last 7 days.");
  assert.equal(finishedRangeText(30), "Finished or dropped in the last 30 days.");
  assert.equal(finishedRangeText(1), "Finished or dropped in the last day.");
});

test("each agent keeps its own colour and the owner is a separate tile", () => {
  const known = assigneesOn(board([task("a", { assignee: "agent:scout" }), task("b"), task("c", { assignee: "agent:archivist" })]));
  assert.deepEqual(known, ["owner", "agent:archivist", "agent:scout"]);
  assert.deepEqual(tileFor("owner", known), { kind: "owner" });
  assert.deepEqual(tileFor("agent:archivist", known), { kind: "agent", id: "archivist", letter: "A", tone: 0 });
  assert.deepEqual(tileFor("agent:scout", known), { kind: "agent", id: "scout", letter: "S", tone: 1 });
  assert.equal(tileFor("agent:new", known).kind, "agent");
});

// ---- repeat ----

test("the picker writes the common repeat rules", () => {
  const base = emptyRepeat(15);
  assert.equal(ruleFor(base), null);
  assert.equal(ruleFor({ ...base, mode: "daily" }), "FREQ=DAILY");
  assert.equal(ruleFor({ ...base, mode: "weekdays" }), "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR");
  assert.equal(ruleFor({ ...base, mode: "weekly", days: ["TH", "MO"] }), "FREQ=WEEKLY;BYDAY=MO,TH");
  assert.equal(ruleFor({ ...base, mode: "weekly" }), "FREQ=WEEKLY");
  assert.equal(ruleFor({ ...base, mode: "biweekly" }), "FREQ=WEEKLY;INTERVAL=2");
  assert.equal(ruleFor({ ...base, mode: "monthly" }), "FREQ=MONTHLY;BYMONTHDAY=15");
  assert.equal(ruleFor({ ...base, mode: "yearly" }), "FREQ=YEARLY");
  assert.equal(ruleFor({ ...base, mode: "advanced", raw: "  FREQ=MONTHLY;BYDAY=1MO " }), "FREQ=MONTHLY;BYDAY=1MO");
  assert.equal(ruleFor({ ...base, mode: "advanced", raw: "  " }), null);
});

test("a stored rule is read back into the picker, or kept raw when it is unusual", () => {
  assert.equal(choiceFor(null).mode, "none");
  assert.equal(choiceFor("FREQ=DAILY").mode, "daily");
  assert.equal(choiceFor("rrule:freq=daily;interval=1").mode, "daily");
  assert.equal(choiceFor("FREQ=WEEKLY;BYDAY=FR,MO,TU,WE,TH").mode, "weekdays");
  assert.deepEqual(choiceFor("FREQ=WEEKLY;BYDAY=TH,MO").days, ["MO", "TH"]);
  assert.equal(choiceFor("FREQ=WEEKLY").mode, "weekly");
  assert.equal(choiceFor("FREQ=WEEKLY;INTERVAL=2").mode, "biweekly");
  assert.equal(choiceFor("FREQ=MONTHLY;BYMONTHDAY=2").monthDay, 2);
  assert.equal(choiceFor("FREQ=YEARLY").mode, "yearly");
  for (const rule of ["FREQ=DAILY;COUNT=5", "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO", "FREQ=MONTHLY", "FREQ=MONTHLY;BYDAY=1MO", "FREQ=WEEKLY;BYDAY=1MO", "nonsense"]) {
    const choice = choiceFor(rule);
    assert.equal(choice.mode, "advanced", rule);
    assert.equal(choice.raw, rule);
  }
});

test("reading a rule and writing it again does not change it", () => {
  for (const rule of ["FREQ=DAILY", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "FREQ=WEEKLY;BYDAY=TU,SA", "FREQ=WEEKLY;INTERVAL=2", "FREQ=MONTHLY;BYMONTHDAY=31", "FREQ=YEARLY", "FREQ=DAILY;UNTIL=20310101"]) {
    assert.equal(ruleFor(choiceFor(rule)), rule);
  }
});

test("weekday choices toggle, stay in order and start on the locale's first day", () => {
  assert.deepEqual(toggleDay(["MO"], "WE"), ["MO", "WE"]);
  assert.deepEqual(toggleDay(["WE"], "MO"), ["MO", "WE"]);
  assert.deepEqual(toggleDay(["MO", "WE"], "MO"), ["WE"]);
  assert.deepEqual(weekdaysFrom(7), ["SU", "MO", "TU", "WE", "TH", "FR", "SA"]);
  assert.deepEqual(weekdaysFrom(1)[0], "MO");
  assert.deepEqual(weekdaysFrom(99)[0], "MO");
  assert.equal(weekdayName("MO", "en-US"), "Mon");
  assert.equal(weekdayName("SU", "en-US", "long"), "Sunday");
});

// ---- form ----

test("a new task's form starts with the default area and the owner", () => {
  const form = formFor(null, { area: "home", today: TODAY });
  assert.equal(form.area, "home");
  assert.equal(form.assignee, "owner");
  assert.equal(form.repeat.monthDay, 15);
  assert.deepEqual(validateForm(form), { title: "Give the task a title." });
});

test("the form refuses a time without a date and an empty advanced rule", () => {
  const form = formFor(null, { area: "", today: TODAY });
  const timed = validateForm({ ...form, title: "Call back", dueTime: "09:30" });
  assert.ok(timed.dueTime);
  assert.deepEqual(validateForm({ ...form, title: "Call back", dueTime: "09:30", due: TODAY }), {});
  assert.deepEqual(validateForm({ ...form, title: "Call back", dueTime: "09:30", repeat: { ...form.repeat, mode: "daily" } }), {});
  assert.ok(validateForm({ ...form, title: "Call back", repeat: { ...form.repeat, mode: "advanced" } }).repeat);
  assert.ok(validateForm({ ...form, title: "x".repeat(301) }).title);
  assert.ok(validateForm({ ...form, title: "ok", area: "a".repeat(41) }).area);
});

test("a new task is sent with a tidy title, a lower-case area and nulls for blanks", () => {
  const form = formFor(null, { area: "", today: TODAY });
  assert.deepEqual(createBody({ ...form, title: "  Water   the plants ", area: " Home " }), {
    title: "Water the plants",
    notes: "",
    area: "home",
    due: null,
    due_time: null,
    repeat: null,
    assignee: "owner",
  });
});

test("an edit sends only what changed, so an untouched schedule is left alone", () => {
  const existing = task("a", { title: "Water the plants", area: "home", due: "2030-01-20", repeat: "FREQ=WEEKLY;INTERVAL=2", due_time: "08:00" });
  const form = formFor(existing, { area: "", today: TODAY });
  assert.equal(hasChanges(changesBody(form, existing)), false);
  assert.deepEqual(changesBody({ ...form, title: "Water every plant", dueTime: "" }, existing), { title: "Water every plant", due_time: null });
  assert.deepEqual(changesBody({ ...form, due: "", repeat: { ...form.repeat, mode: "none" } }, existing), { due: null, repeat: null });
  assert.deepEqual(changesBody({ ...form, area: "" }, existing), { area: "" });
});

test("an unusual stored rule survives an edit of something else", () => {
  const existing = task("a", { due: "2030-01-20", repeat: "FREQ=MONTHLY;BYDAY=1MO" });
  const form = formFor(existing, { area: "", today: TODAY });
  assert.equal(form.repeat.mode, "advanced");
  assert.deepEqual(changesBody({ ...form, notes: "Bring the folder" }, existing), { notes: "Bring the folder" });
});

// ---- optimistic ----

test("ticking a one-off moves it to done at once", () => {
  const start = board([task("a", { due: TODAY }), task("b")]);
  const after = applyDone(start, "a", 99);
  assert.deepEqual(after.open.map((item) => item.id), ["b"]);
  assert.deepEqual(after.today, []);
  assert.equal(after.done[0]?.id, "a");
  assert.equal(after.done[0]?.status, "done");
  assert.equal(after.done[0]?.done_at, 99);
  assert.equal(after.counts.open, 1);
  assert.equal(after.counts.done_today, 1);
  assert.equal(start.open.length, 2);
});

test("ticking a repeating task keeps the round and leaves the task open", () => {
  const start = board([task("a", { due: TODAY, repeat: "FREQ=DAILY", pushes: 2 })]);
  const after = applyDone(start, "a", 99);
  assert.deepEqual(after.open.map((item) => item.id), ["a"]);
  assert.deepEqual(after.today, []);
  assert.deepEqual(after.kept_today.map((item) => item.id), ["a"]);
  assert.equal(after.kept_today[0]?.last_kept_on, TODAY);
  assert.equal(after.kept_today[0]?.can_undo_round, true);
  assert.equal(after.counts.done_today, 1);
});

test("dropping takes a task off the open list", () => {
  const after = applyDrop(board([task("a", { due: TODAY }), task("b")]), "a", 5);
  assert.deepEqual(after.open.map((item) => item.id), ["b"]);
  assert.equal(after.dropped[0]?.status, "dropped");
  assert.equal(after.dropped[0]?.dropped_at, 5);
  assert.deepEqual(after.today, []);
});

test("pushing moves the due date, counts the push and marks a stall", () => {
  const start = board([task("a", { due: TODAY, pushes: 2 })]);
  const after = applyPush(start, "a", "2030-01-16");
  assert.equal(after.open[0]?.due, "2030-01-16");
  assert.equal(after.open[0]?.pushes, 3);
  assert.equal(after.open[0]?.stalled, true);
  assert.equal(after.open[0]?.due_today, false);
  assert.deepEqual(after.today, []);
  assert.equal(after.counts.stalled, 1);
});

test("ranking moves a task next to another in the one manual order", () => {
  const start = board([task("a", { due: TODAY }), task("b"), task("c", { due: TODAY }), task("d")]);
  assert.deepEqual(applyRank(start, "c", { before: "a" }).open.map((item) => item.id), ["c", "a", "b", "d"]);
  assert.deepEqual(applyRank(start, "a", { after: "c" }).open.map((item) => item.id), ["b", "c", "a", "d"]);
  assert.deepEqual(applyRank(start, "a", { after: "c" }).today.map((item) => item.id), ["c", "a"]);
  assert.equal(applyRank(start, "a", { after: "zzz" }), start);
  assert.equal(applyRank(start, "a", { after: "a" }), start);
});

test("moving a row up or down ranks it against its neighbour, and stops at the edge", () => {
  const ids = ["a", "b", "c"];
  assert.deepEqual(stepTarget(ids, "b", -1), { before: "a" });
  assert.deepEqual(stepTarget(ids, "b", 1), { after: "c" });
  assert.equal(stepTarget(ids, "a", -1), null);
  assert.equal(stepTarget(ids, "c", 1), null);
  assert.equal(stepTarget(ids, "zzz", 1), null);
});

test("dropping a dragged row where it already sits does nothing", () => {
  const ids = ["a", "b", "c"];
  assert.equal(dropTarget(ids, "a", "a", "before"), null);
  assert.equal(dropTarget(ids, "a", "b", "before"), null);
  assert.equal(dropTarget(ids, "b", "a", "after"), null);
  assert.deepEqual(dropTarget(ids, "a", "b", "after"), { after: "b" });
  assert.deepEqual(dropTarget(ids, "c", "a", "before"), { before: "a" });
  assert.equal(dropTarget(ids, "c", "zzz", "before"), null);
});

test("a round shows as kept only until the task moves on to its next round", () => {
  const early = board([task("a", { due: "2030-01-16", repeat: "FREQ=DAILY" })]);
  const pending = applyDone(early, "a", 99).open[0];
  assert.ok(pending);
  // Ticked at once, even when the round was not due until tomorrow.
  assert.equal(roundKeptToday(pending, TODAY), true);
  // The server's answer: the same round is recorded as kept, and the task is due later.
  assert.equal(roundKeptToday({ ...pending, due: "2030-01-17" }, TODAY), false);
  assert.equal(roundKeptToday(task("b", { due: TODAY, repeat: "FREQ=DAILY", last_kept: "2030-01-14", last_kept_on: "2030-01-14" }), TODAY), false);
  assert.equal(roundKeptToday(task("c", { due: TODAY, last_kept: TODAY, last_kept_on: TODAY }), TODAY), false);
});

test("reopening a finished one-off puts it back at the end of the open list at once", () => {
  const finished = task("a", { due: "2030-01-13", status: "done", done_at: Date.UTC(2030, 0, 15, 9), next_due: null });
  const start = board([task("b", { due: TODAY })], { done: [finished], counts: { open: 1, due_today: 1, lapsed: 0, stalled: 0, done_today: 1 } });
  const after = applyReopen(start, "a");
  assert.deepEqual(after.open.map((item) => item.id), ["b", "a"]);
  assert.deepEqual(after.done, []);
  assert.equal(after.open[1]?.status, "open");
  assert.equal(after.open[1]?.done_at, null);
  assert.equal(after.open[1]?.lapsed_days, 2);
  assert.deepEqual(after.today.map((item) => item.id), ["b", "a"]);
  assert.equal(after.counts.open, 2);
  assert.equal(after.counts.lapsed, 1);
  assert.equal(after.counts.done_today, 0);
  // A repeating task that ran out is the server's to bring back: it undoes a round instead.
  const ran = board([], { done: [task("r", { status: "done", repeat: "FREQ=DAILY;COUNT=1", done_at: 5 })] });
  assert.equal(applyReopen(ran, "r"), ran);
  assert.equal(applyReopen(start, "zzz"), start);
});

test("a one-off ticked now counts as done today and reopening it takes that back, in any browser zone", () => {
  const start = board([task("a", { due: TODAY })], { timezone: "local" });
  const ticked = applyDone(start, "a", Date.UTC(2030, 0, 16, 13)); // the browser's clock is on another day
  assert.equal(ticked.done[0]?.done_on, TODAY);
  assert.equal(ticked.counts.done_today, 1);
  assert.deepEqual(todayList(ticked).items.map((item) => [item.task.id, item.state]), [["a", "done"]]);
  const back = applyReopen(ticked, "a");
  assert.equal(back.counts.done_today, 0);
  assert.equal(back.open[0]?.done_on, null);
  // One finished on an earlier day leaves today's count alone.
  const old = board([], { timezone: "local", done: [task("b", { status: "done", done_at: Date.UTC(2030, 0, 15, 9), done_on: "2030-01-14" })], counts: { open: 0, due_today: 0, lapsed: 0, stalled: 0, done_today: 1 } });
  assert.equal(applyReopen(old, "b").counts.done_today, 1);
});

test("a pushed task can have its push taken back", () => {
  const after = applyPush(board([task("a", { due: TODAY })]), "a", "2030-01-16");
  assert.equal(after.open[0]?.can_unpush, true);
});

test("undo runs the latest offer once, from the toast or the key, and only while its toast is up", () => {
  const ran: string[] = [];
  const first = offerUndo(() => ran.push("first"), 1000);
  const second = offerUndo(() => ran.push("second"), 2000);
  assert.equal(undoLatest(2500), true);
  assert.deepEqual(ran, ["second"]);
  second(); // the toast's button after the key: nothing more happens
  assert.deepEqual(ran, ["second"]);
  assert.equal(undoLatest(2600), false); // the key never reaches back to an older offer
  first(); // but its own toast button still works, once
  first();
  assert.deepEqual(ran, ["second", "first"]);
  const pressed = offerUndo(() => ran.push("third"), 3000);
  pressed(); // the button first: the key then has nothing to undo
  assert.equal(undoLatest(3100), false);
  offerUndo(() => ran.push("late"), 4000);
  assert.equal(undoLatest(4000 + UNDO_LIFETIME_MS + 1), false);
  assert.deepEqual(ran, ["second", "first", "third"]);
});

test("restoring a dropped task puts it back on the open list with its pushes", () => {
  const gone = task("a", { due: TODAY, status: "dropped", dropped_at: 7, next_due: null, due_today: false, pushes: 3 });
  const after = applyRestore(board([task("b")], { dropped: [gone] }), "a");
  assert.deepEqual(after.open.map((item) => item.id), ["b", "a"]);
  assert.deepEqual(after.dropped, []);
  assert.equal(after.open[1]?.status, "open");
  assert.equal(after.open[1]?.dropped_at, null);
  assert.equal(after.open[1]?.due_today, true);
  assert.equal(after.open[1]?.stalled, true);
  assert.deepEqual(after.today.map((item) => item.id), ["a"]);
});

test("undoing a kept round makes the task due again at once", () => {
  const kept = task("a", { due: "2030-01-16", next_due: "2030-01-16", repeat: "FREQ=DAILY", last_kept: TODAY, last_kept_on: TODAY, kept_count: 1, can_undo_round: true });
  const start = board([kept], { kept_today: [kept], counts: { open: 1, due_today: 0, lapsed: 0, stalled: 0, done_today: 1 } });
  const after = applyUndoRound(start, "a");
  assert.equal(after.open[0]?.due, TODAY);
  assert.equal(after.open[0]?.due_today, true);
  assert.equal(after.open[0]?.kept_count, 0);
  assert.equal(after.open[0]?.can_undo_round, false);
  assert.deepEqual(after.kept_today, []);
  assert.deepEqual(after.today.map((item) => item.id), ["a"]);
  assert.equal(after.counts.done_today, 0);
  assert.equal(after.counts.due_today, 1);
  const none = board([task("b", { due: TODAY, repeat: "FREQ=DAILY" })]);
  assert.equal(applyUndoRound(none, "b"), none);
});

// ---- keys ----

const FREE = { typing: false, onControl: false, dialogOpen: false };

test("the list keys move, tick, edit, push and add", () => {
  assert.equal(listAction({ key: "j" }, FREE), "next");
  assert.equal(listAction({ key: "ArrowDown" }, FREE), "next");
  assert.equal(listAction({ key: "k" }, FREE), "prev");
  assert.equal(listAction({ key: "ArrowUp" }, FREE), "prev");
  assert.equal(listAction({ key: "x" }, FREE), "tick");
  assert.equal(listAction({ key: " " }, FREE), "tick");
  assert.equal(listAction({ key: "e" }, FREE), "edit");
  assert.equal(listAction({ key: "n" }, FREE), "new");
  assert.equal(listAction({ key: "p" }, FREE), "push");
  assert.equal(listAction({ key: "Enter" }, FREE), "open");
  assert.equal(listAction({ key: "u" }, FREE), "undo");
  assert.equal(listAction({ key: "ArrowUp", altKey: true }, FREE), "moveUp");
  assert.equal(listAction({ key: "ArrowDown", altKey: true }, FREE), "moveDown");
  assert.equal(listAction({ key: "q" }, FREE), null);
});

test("keys are left alone while typing, in a dialog or with a command key held", () => {
  assert.equal(listAction({ key: "n" }, { ...FREE, typing: true }), null);
  assert.equal(listAction({ key: "x" }, { ...FREE, dialogOpen: true }), null);
  assert.equal(listAction({ key: "k", metaKey: true }, FREE), null);
  assert.equal(listAction({ key: "p", ctrlKey: true }, FREE), null);
  assert.equal(listAction({ key: "e", altKey: true }, FREE), null);
});

test("a held key keeps moving the selection but never ticks, pushes or reorders again", () => {
  assert.equal(listAction({ key: "j", repeat: true }, FREE), "next");
  assert.equal(listAction({ key: "ArrowUp", repeat: true }, FREE), "prev");
  for (const key of ["x", " ", "p", "e", "n", "u", "Enter"]) assert.equal(listAction({ key, repeat: true }, FREE), null, key);
  assert.equal(listAction({ key: "ArrowDown", altKey: true, repeat: true }, FREE), null);
});

test("space and enter still press the button that has focus", () => {
  const onButton = { ...FREE, onControl: true };
  assert.equal(listAction({ key: " " }, onButton), null);
  assert.equal(listAction({ key: "Enter" }, onButton), null);
  assert.equal(listAction({ key: "x" }, onButton), "tick");
  assert.equal(listAction({ key: "j" }, onButton), "next");
});

test("selection steps through the rows and stops at the ends", () => {
  const ids = ["a", "b", "c"];
  assert.equal(stepSelection(ids, null, 1), "a");
  assert.equal(stepSelection(ids, null, -1), "c");
  assert.equal(stepSelection(ids, "a", 1), "b");
  assert.equal(stepSelection(ids, "c", 1), "c");
  assert.equal(stepSelection(ids, "a", -1), "a");
  assert.equal(stepSelection(ids, "gone", 1), "a");
  assert.equal(stepSelection([], null, 1), null);
  assert.equal(selectionAfterRemoval(ids, "b"), "c");
  assert.equal(selectionAfterRemoval(ids, "c"), "b");
  assert.equal(selectionAfterRemoval(["a"], "a"), null);
});

// ---- api ----

test("board filters are sent as query parameters only when set", () => {
  assert.equal(boardPath({ assignee: null, area: null }), "/api/tasks");
  assert.equal(boardPath({ assignee: "agent:scout", area: null }), "/api/tasks?assignee=agent%3Ascout");
  assert.equal(boardPath({ assignee: "owner", area: "home & garden" }), "/api/tasks?assignee=owner&area=home+%26+garden");
});

// ---- an agent's chores are not the owner's list ----

test("the tasks screens start on the owner's own tasks, and a filter is only narrower when it is an area or another person", () => {
  assert.equal(OWNER_FILTER.assignee, "owner");
  assert.equal(narrowsBeyondDefault(OWNER_FILTER), false);
  assert.equal(narrowsBeyondDefault({ assignee: null, area: null }), false);
  assert.equal(narrowsBeyondDefault({ assignee: "agent:scout", area: null }), true);
  assert.equal(narrowsBeyondDefault({ assignee: "owner", area: "home" }), true);
});

test("the owner's list says how many open tasks belong to agents, and says nothing once everyone is shown", () => {
  const everything = board([task("a"), task("b", { assignee: "agent:scout" }), task("c", { assignee: "agent:scout" })]);
  assert.equal(agentTasksHidden(everything, OWNER_FILTER), 2);
  assert.equal(agentTasksHidden(everything, { assignee: null }), 0);
  assert.equal(agentTasksHidden(undefined, OWNER_FILTER), 0);
});

test("the board's Done for you column still holds what agents finished while the list shows only the owner's", () => {
  const mine = board([task("a")]);
  const everything = board([], { done: [task("d", { assignee: "agent:scout", status: "done" }), task("e", { status: "done" })] });
  const columns = boardColumns(withAgentsDone(mine, everything, OWNER_FILTER));
  assert.deepEqual(columns.find((column) => column.id === "done_for_you")?.tasks.map((item) => item.id), ["d"]);
  assert.equal(withAgentsDone(mine, everything, { assignee: null }), mine);
});
