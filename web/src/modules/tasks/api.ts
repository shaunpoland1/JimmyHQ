// Typed client for the tasks API (`/api/tasks`). Dates are "YYYY-MM-DD" in the
// owner's calendar; timestamps are unix milliseconds UTC.

import { request } from "../../api.ts";

export type TaskStatus = "open" | "done" | "dropped";

export type Task = {
  /** 26-character ULID. */
  id: string;
  title: string;
  notes: string;
  /** Lower-cased tag; "" means none. */
  area: string;
  due: string | null;
  /** "HH:MM", 24-hour; only set when `due` is set. */
  due_time: string | null;
  /** Canonical RRULE without the "RRULE:" prefix. */
  repeat: string | null;
  /** The rule in words, e.g. "every Thursday"; null when not repeating. */
  repeat_text: string | null;
  /** "owner" or "agent:<id>". */
  assignee: string;
  status: TaskStatus;
  created: number;
  updated: number;
  done_at: number | null;
  /**
   * The owner's calendar day the task was done on, worked out by the server; null
   * while it is not done. Compare it with the board's `date`; never work a day out
   * from `done_at`. Optional only so that task fixtures written before it existed
   * still type-check: the server always sends it.
   */
  done_on?: string | null;
  dropped_at: number | null;
  filed_by: string;
  next_due: string | null;
  due_today: boolean;
  lapsed_days: number;
  pushes: number;
  /** True while the latest push can be taken back (`unpush`). The server always sends it. */
  can_unpush?: boolean;
  stalled: boolean;
  last_kept: string | null;
  last_kept_on: string | null;
  kept_count: number;
  can_undo_round: boolean;
};

export type TaskCounts = {
  open: number;
  due_today: number;
  lapsed: number;
  stalled: number;
  done_today: number;
};

export type TaskBoard = {
  /** Today in the owner's time zone. */
  date: string;
  /** IANA name, or "local" for the server computer's own zone (not the browser's). */
  timezone: string;
  stalled_after: number;
  /** How many days of finished and dropped tasks `done` and `dropped` cover. */
  done_days: number;
  open: Task[];
  today: Task[];
  kept_today: Task[];
  done: Task[];
  dropped: Task[];
  areas: string[];
  default_area: string;
  counts: TaskCounts;
};

export type BoardFilter = { assignee: string | null; area: string | null };

export type NewTask = {
  title: string;
  notes?: string;
  /** null or omitted uses the default area; "" means none. */
  area?: string | null;
  due?: string | null;
  due_time?: string | null;
  repeat?: string | null;
  assignee?: string;
};

export type TaskChanges = {
  title?: string;
  notes?: string | null;
  area?: string | null;
  due?: string | null;
  due_time?: string | null;
  repeat?: string | null;
  assignee?: string;
};

export type RankTarget = { before: string } | { after: string };

function taskPath(id: string, action?: string): string {
  const base = `/api/tasks/${encodeURIComponent(id)}`;
  return action ? `${base}/${action}` : base;
}

export function boardPath(filter: BoardFilter): string {
  const params = new URLSearchParams();
  if (filter.assignee) params.set("assignee", filter.assignee);
  if (filter.area) params.set("area", filter.area);
  const query = params.toString();
  return query ? `/api/tasks?${query}` : "/api/tasks";
}

export const tasksApi = {
  board: (filter: BoardFilter) => request<TaskBoard>("GET", boardPath(filter)),
  get: (id: string) => request<Task>("GET", taskPath(id)),
  create: (body: NewTask) => request<Task>("POST", "/api/tasks", { body }),
  edit: (id: string, body: TaskChanges) => request<Task>("PATCH", taskPath(id), { body }),
  done: (id: string) => request<Task>("POST", taskPath(id, "done")),
  reopen: (id: string) => request<Task>("POST", taskPath(id, "reopen")),
  undoRound: (id: string) => request<Task>("POST", taskPath(id, "undo-round")),
  push: (id: string, to: string) => request<Task>("POST", taskPath(id, "push"), { body: { to } }),
  /** Takes back the latest push: the earlier due date and push count return. */
  unpush: (id: string) => request<Task>("POST", taskPath(id, "unpush")),
  drop: (id: string) => request<Task>("POST", taskPath(id, "drop")),
  restore: (id: string) => request<Task>("POST", taskPath(id, "restore")),
  rank: (id: string, target: RankTarget) => request<Task>("POST", taskPath(id, "rank"), { body: target }),
};
