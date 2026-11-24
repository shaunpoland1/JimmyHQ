// What the board looks like the instant an action is taken, before the server
// answers. The server's answer always replaces these guesses.

import type { RankTarget, Task, TaskBoard } from "../api.ts";
import { daysBetween } from "./dates.ts";
import { doneOn } from "./group.ts";

function recount(board: TaskBoard, doneToday: number): TaskBoard {
  const today = board.open.filter((task) => task.due_today || task.lapsed_days > 0);
  return {
    ...board,
    today,
    counts: {
      open: board.open.length,
      due_today: board.open.filter((task) => task.due_today).length,
      lapsed: board.open.filter((task) => task.lapsed_days > 0).length,
      stalled: board.open.filter((task) => task.stalled).length,
      done_today: Math.max(0, doneToday),
    },
  };
}

function without(tasks: readonly Task[], id: string): Task[] {
  return tasks.filter((task) => task.id !== id);
}

/** Done for a one-off; this round kept for a repeating task. */
export function applyDone(board: TaskBoard, id: string, now: number): TaskBoard {
  const task = board.open.find((item) => item.id === id);
  if (!task) return board;
  if (!task.repeat) {
    const done: Task = { ...task, status: "done", done_at: now, done_on: board.date, next_due: null, due_today: false, lapsed_days: 0, pushes: 0, can_unpush: false, stalled: false };
    return recount({ ...board, open: without(board.open, id), done: [done, ...board.done] }, board.counts.done_today + 1);
  }
  // The next due date is the server's to work out; until it answers the task
  // simply stops being due.
  const kept: Task = {
    ...task,
    due_today: false,
    lapsed_days: 0,
    pushes: 0,
    can_unpush: false,
    stalled: false,
    last_kept: task.due,
    last_kept_on: board.date,
    kept_count: task.kept_count + 1,
    can_undo_round: true,
  };
  return recount(
    {
      ...board,
      open: board.open.map((item) => (item.id === id ? kept : item)),
      kept_today: [...without(board.kept_today, id), kept],
    },
    board.counts.done_today + 1,
  );
}

export function applyDrop(board: TaskBoard, id: string, now: number): TaskBoard {
  const task = board.open.find((item) => item.id === id);
  if (!task) return board;
  const dropped: Task = { ...task, status: "dropped", dropped_at: now, next_due: null, due_today: false, lapsed_days: 0, stalled: false };
  return recount(
    { ...board, open: without(board.open, id), kept_today: without(board.kept_today, id), dropped: [dropped, ...board.dropped] },
    board.counts.done_today,
  );
}

export function applyPush(board: TaskBoard, id: string, to: string): TaskBoard {
  const open = board.open.map((task) => {
    if (task.id !== id) return task;
    const pushes = task.pushes + 1;
    return { ...task, due: to, next_due: to, due_today: to === board.date, lapsed_days: 0, pushes, can_unpush: true, stalled: pushes >= board.stalled_after };
  });
  return recount({ ...board, open }, board.counts.done_today);
}

/** A task as it stands once it is open again with the given due date. */
function opened(task: Task, due: string | null, board: TaskBoard): Task {
  const late = due ? daysBetween(due, board.date) : 0;
  return {
    ...task,
    status: "open",
    done_at: null,
    done_on: null,
    dropped_at: null,
    due,
    next_due: due,
    due_today: due === board.date,
    lapsed_days: Math.max(0, late),
    stalled: task.pushes >= board.stalled_after,
  };
}

/**
 * A finished one-off goes back to the end of the open list. A repeating task
 * that ran out is left for the server, which undoes its last round instead.
 */
export function applyReopen(board: TaskBoard, id: string): TaskBoard {
  const task = board.done.find((item) => item.id === id);
  if (!task || task.repeat) return board;
  const today = doneOn(task, board.timezone) === board.date;
  return recount(
    { ...board, done: without(board.done, id), open: [...without(board.open, id), opened({ ...task, pushes: 0, can_unpush: false }, task.due, board)] },
    board.counts.done_today - (today ? 1 : 0),
  );
}

/** A dropped task goes back to the end of the open list, pushes and history kept. */
export function applyRestore(board: TaskBoard, id: string): TaskBoard {
  const task = board.dropped.find((item) => item.id === id);
  if (!task) return board;
  return recount({ ...board, dropped: without(board.dropped, id), open: [...without(board.open, id), opened(task, task.due, board)] }, board.counts.done_today);
}

/**
 * Takes back the latest kept round of an open task: it is due again on the
 * date that round was for. Which round came before is the server's to say.
 */
export function applyUndoRound(board: TaskBoard, id: string): TaskBoard {
  const task = board.open.find((item) => item.id === id);
  if (!task || task.last_kept === null) return board;
  const today = task.last_kept_on === board.date;
  const kept = Math.max(0, task.kept_count - 1);
  const back: Task = { ...opened(task, task.last_kept, board), last_kept: null, last_kept_on: null, kept_count: kept, can_undo_round: kept > 0 };
  return recount(
    { ...board, open: board.open.map((item) => (item.id === id ? back : item)), kept_today: without(board.kept_today, id) },
    board.counts.done_today - (today ? 1 : 0),
  );
}

/** Moves a task to sit immediately before or after another in the one manual order. */
export function applyRank(board: TaskBoard, id: string, target: RankTarget): TaskBoard {
  const other = "before" in target ? target.before : target.after;
  const moving = board.open.find((task) => task.id === id);
  if (!moving || other === id || !board.open.some((task) => task.id === other)) return board;
  const rest = without(board.open, id);
  const at = rest.findIndex((task) => task.id === other);
  rest.splice("before" in target ? at : at + 1, 0, moving);
  return recount({ ...board, open: rest }, board.counts.done_today);
}

/** The rank call that moves a row one place up or down among the rows shown; null at the edge. */
export function stepTarget(ids: readonly string[], id: string, direction: -1 | 1): RankTarget | null {
  const at = ids.indexOf(id);
  if (at < 0) return null;
  const other = ids[at + direction];
  if (other === undefined) return null;
  return direction < 0 ? { before: other } : { after: other };
}

/** The rank call for dropping a dragged row on another; null when nothing would move. */
export function dropTarget(ids: readonly string[], id: string, over: string, side: "before" | "after"): RankTarget | null {
  if (id === over) return null;
  const from = ids.indexOf(id);
  const to = ids.indexOf(over);
  if (from < 0 || to < 0) return null;
  if (side === "before" && to === from + 1) return null;
  if (side === "after" && to === from - 1) return null;
  return side === "before" ? { before: over } : { after: over };
}
