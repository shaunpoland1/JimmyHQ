// Grouping, filtering and counting of tasks. Nothing here re-sorts: every view
// keeps the server's one manual order.

import type { Task, TaskBoard } from "../api.ts";
import { dayOfTimestamp, daysBetween } from "./dates.ts";
import { NO_NAMES, shownInitials, shownName } from "../../../lib/agentNames.ts";
import type { ChosenNames } from "../../../lib/agentNames.ts";

export type GroupId = "today" | "tomorrow" | "week" | "later" | "none";
export type TaskGroup = { id: GroupId; label: string; tasks: Task[] };

export const GROUP_LABELS: Record<GroupId, string> = {
  today: "Today",
  tomorrow: "Tomorrow",
  week: "This week",
  later: "Later",
  none: "No date",
};

const GROUP_ORDER: readonly GroupId[] = ["today", "tomorrow", "week", "later", "none"];

/** How many days ahead still counts as "this week" (and as "Next" on the board). */
export const WEEK_DAYS = 7;

/** Which group an open task belongs to. Lapsed tasks sit under Today. */
export function groupOf(task: Pick<Task, "due">, today: string): GroupId {
  if (!task.due) return "none";
  const ahead = daysBetween(today, task.due);
  if (ahead <= 0) return "today";
  if (ahead === 1) return "tomorrow";
  if (ahead <= WEEK_DAYS) return "week";
  return "later";
}

/**
 * True while a repeating task's current round is the one kept today: the tick
 * has been pressed and the server has not yet moved the task to its next round.
 */
export function roundKeptToday(task: Pick<Task, "repeat" | "due" | "last_kept" | "last_kept_on">, today: string): boolean {
  return task.repeat !== null && task.due !== null && task.last_kept_on === today && task.last_kept === task.due;
}

/** Open tasks in their groups, empty groups left out, manual order kept inside each. */
export function groupOpen(tasks: readonly Task[], today: string): TaskGroup[] {
  const buckets = new Map<GroupId, Task[]>();
  for (const task of tasks) {
    const id = groupOf(task, today);
    const bucket = buckets.get(id);
    if (bucket) bucket.push(task);
    else buckets.set(id, [task]);
  }
  return GROUP_ORDER.flatMap((id) => {
    const bucket = buckets.get(id);
    return bucket ? [{ id, label: GROUP_LABELS[id], tasks: bucket }] : [];
  });
}

/** Tasks whose title or area contains every word of the query. */
export function searchTasks(tasks: readonly Task[], query: string): Task[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...tasks];
  return tasks.filter((task) => {
    const text = `${task.title} ${task.area}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
}

export type BoardColumnId = "now" | "next" | "later" | "done_for_you";
export type BoardColumn = { id: BoardColumnId; label: string; tasks: Task[] };

export function isAgent(assignee: string): boolean {
  return assignee.startsWith("agent:");
}

/** What the tasks screens start on: the owner's own tasks. An agent's chores are one choice away ("Everyone"). */
export const OWNER_FILTER: { assignee: string | null; area: string | null } = { assignee: "owner", area: null };

/** Whether a filter narrows the list beyond what the screens start on: an area, or one person other than the owner. */
export function narrowsBeyondDefault(filter: { assignee: string | null; area: string | null }): boolean {
  return filter.area !== null || (filter.assignee !== null && filter.assignee !== "owner");
}

/** How many open tasks belong to agents, for the line that says they are not shown. */
export function agentTasksHidden(everything: Pick<TaskBoard, "open"> | undefined, filter: { assignee: string | null }): number {
  if (!everything || filter.assignee !== "owner") return 0;
  return everything.open.filter((task) => isAgent(task.assignee)).length;
}

/**
 * The board view's columns for the owner's default view: their own tasks, and in "Done for you"
 * the finished work of agents, which the owner's own list does not carry.
 */
export function withAgentsDone(board: TaskBoard, everything: Pick<TaskBoard, "done"> | undefined, filter: { assignee: string | null }): TaskBoard {
  if (!everything || filter.assignee !== "owner") return board;
  const have = new Set(board.done.map((task) => task.id));
  return { ...board, done: [...board.done, ...everything.done.filter((task) => isAgent(task.assignee) && !have.has(task.id))] };
}

/** The four board columns. "Done for you" is finished work whose assignee is an agent. */
export function boardColumns(board: Pick<TaskBoard, "open" | "done" | "date">): BoardColumn[] {
  const now: Task[] = [];
  const next: Task[] = [];
  const later: Task[] = [];
  for (const task of board.open) {
    const group = groupOf(task, board.date);
    if (group === "today") now.push(task);
    else if (group === "tomorrow" || group === "week") next.push(task);
    else later.push(task);
  }
  return [
    { id: "now", label: "Now", tasks: now },
    { id: "next", label: "Next", tasks: next },
    { id: "later", label: "Later", tasks: later },
    { id: "done_for_you", label: "Done for you", tasks: board.done.filter((task) => isAgent(task.assignee)) },
  ];
}

export type PlanCounts = { now: number; next: number; later: number };

export function planCounts(board: Pick<TaskBoard, "open" | "date">): PlanCounts {
  const counts: PlanCounts = { now: 0, next: 0, later: 0 };
  for (const task of board.open) {
    const group = groupOf(task, board.date);
    if (group === "today") counts.now += 1;
    else if (group === "tomorrow" || group === "week") counts.next += 1;
    else counts.later += 1;
  }
  return counts;
}

/**
 * The owner's calendar day a task was done on. The server says it (`done_on`).
 * Only for a task that came without one is the day read from `done_at`, and then
 * only in a zone the server named: "local" is the server computer's zone, which
 * the browser cannot know, so such a task has no day here.
 */
export function doneOn(task: Pick<Task, "done_on" | "done_at">, timezone: string): string | null {
  if (task.done_on !== undefined) return task.done_on;
  if (task.done_at === null || timezone === "local") return null;
  return dayOfTimestamp(task.done_at, timezone);
}

/** What the Finished view says about how far back it reaches. */
export function finishedRangeText(days: number): string {
  return days === 1 ? "Finished or dropped in the last day." : `Finished or dropped in the last ${days} days.`;
}

export type TodayItem = { task: Task; state: "due" | "kept" | "done" };
export type TodayList = { items: TodayItem[]; done: number; total: number };

/**
 * The Today checklist: what is due (or lapsed), then rounds kept today and
 * one-off tasks finished today, both shown ticked.
 */
export function todayList(board: Pick<TaskBoard, "today" | "kept_today" | "done" | "date" | "timezone">): TodayList {
  const seen = new Set<string>();
  const items: TodayItem[] = [];
  const add = (task: Task, state: TodayItem["state"]) => {
    if (seen.has(task.id)) return;
    seen.add(task.id);
    items.push({ task, state });
  };
  for (const task of board.today) add(task, "due");
  for (const task of board.kept_today) add(task, "kept");
  for (const task of board.done) {
    if (doneOn(task, board.timezone) === board.date) add(task, "done");
  }
  const done = items.filter((item) => item.state !== "due").length;
  return { items, done, total: items.length };
}

/** Every assignee that appears anywhere on a board, "owner" first, then agents by id. */
export function assigneesOn(board: Pick<TaskBoard, "open" | "done" | "dropped">, extra: readonly string[] = []): string[] {
  const found = new Set<string>(extra);
  for (const list of [board.open, board.done, board.dropped]) for (const task of list) found.add(task.assignee);
  found.delete("owner");
  return ["owner", ...[...found].sort()];
}

export type Tile = { kind: "owner" } | { kind: "agent"; letter: string; tone: 0 | 1 | 2 | 3; id: string };

/**
 * The tile for an assignee. Agents take one of four colours by their place in
 * the sorted list of known agents; the owner is the round accent tile. The letters
 * come from the name the agent chose, when it has, as the Village shows them.
 */
export function tileFor(assignee: string, known: readonly string[], chosen: ChosenNames = NO_NAMES): Tile {
  if (!isAgent(assignee)) return { kind: "owner" };
  const id = assignee.slice("agent:".length);
  const agents = known.filter(isAgent);
  const index = Math.max(0, agents.indexOf(assignee));
  return { kind: "agent", id, letter: shownInitials(id, chosen), tone: (index % 4) as 0 | 1 | 2 | 3 };
}

/** "You", or the name an agent chose (its working name until it has). */
export function assigneeLabel(assignee: string, chosen: ChosenNames = NO_NAMES): string {
  return isAgent(assignee) ? shownName(assignee.slice("agent:".length), chosen) : "You";
}
