// The tasks module's data hooks: the board query, the live refresh and the
// actions (optimistic, rolled back with a toast if the server refuses).

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { tasksApi } from "./api";
import type { BoardFilter, RankTarget, Task, TaskBoard } from "./api";
import { formatDay } from "./lib/dates";
import { OWNER_FILTER } from "./lib/group";
import { applyDone, applyDrop, applyPush, applyRank, applyReopen, applyRestore, applyUndoRound } from "./lib/optimistic";
import { offerUndo } from "./lib/undo";

export const TASKS_KEY = ["tasks"] as const;
export const NO_FILTER: BoardFilter = { assignee: null, area: null };
export { OWNER_FILTER };

export function boardKey(filter: BoardFilter) {
  return [...TASKS_KEY, "board", filter.assignee ?? "", filter.area ?? ""] as const;
}

// ---- live refresh ----
// One subscription to the app's shared feed for every tasks view. A task event, or a
// change to a setting the board depends on, marks the board stale.

const REFRESH_DELAY_MS = 150;
const BOARD_REFRESH_MS = 10 * 60_000;

const watchers = new Map<QueryClient, number>();
let stopFeed: (() => void) | null = null;
let pending: ReturnType<typeof setTimeout> | null = null;

function refreshSoon(): void {
  if (pending) return;
  pending = setTimeout(() => {
    pending = null;
    for (const client of watchers.keys()) void client.invalidateQueries({ queryKey: TASKS_KEY });
  }, REFRESH_DELAY_MS);
}

function concernsTasks(kind: string, payload: Record<string, unknown>): boolean {
  if (kind.startsWith("task.")) return true;
  if (kind === "setting.updated" || kind === "setting.reset") {
    const key = payload.key;
    return typeof key === "string" && (key.startsWith("tasks.") || key === "owner.timezone");
  }
  return false;
}

function watch(client: QueryClient): () => void {
  watchers.set(client, (watchers.get(client) ?? 0) + 1);
  stopFeed ??= subscribeFeed({
    onEvent: (event) => {
      if (concernsTasks(event.kind, event.payload ?? {})) refreshSoon();
    },
    // After a reconnect, anything may have been missed.
    onReconnect: refreshSoon,
  });
  return () => {
    const left = (watchers.get(client) ?? 1) - 1;
    if (left > 0) watchers.set(client, left);
    else watchers.delete(client);
    if (watchers.size > 0) return;
    if (pending) clearTimeout(pending);
    pending = null;
    stopFeed?.();
    stopFeed = null;
  };
}

/** Keeps every tasks query in step with changes made anywhere. */
export function useTaskFeed(): void {
  const client = useQueryClient();
  useEffect(() => watch(client), [client]);
}

/** The whole board in one call. Views with the same filter share one query. */
export function useBoard(filter: BoardFilter = OWNER_FILTER) {
  useTaskFeed();
  return useQuery({
    queryKey: boardKey(filter),
    queryFn: () => tasksApi.board(filter),
    placeholderData: keepPreviousData,
    // "Today" changes at midnight without any event.
    refetchOnWindowFocus: true,
    refetchInterval: BOARD_REFRESH_MS,
  });
}

// ---- actions ----

export type TaskActions = {
  /** Done for a one-off; keeps the round of a repeating task. Offers Undo. */
  complete: (task: Task) => Promise<Task | null>;
  drop: (task: Task) => Promise<Task | null>;
  push: (task: Task, to: string) => Promise<Task | null>;
  rank: (task: Task, target: RankTarget) => Promise<Task | null>;
  reopen: (task: Task) => Promise<Task | null>;
  restore: (task: Task) => Promise<Task | null>;
  undoRound: (task: Task) => Promise<Task | null>;
  /** Marks every tasks query stale, after a change made outside these actions. */
  refresh: () => void;
};

function quoted(title: string): string {
  return title.length > 60 ? `“${title.slice(0, 57)}…”` : `“${title}”`;
}

export function useTaskActions(): TaskActions {
  const client = useQueryClient();
  const toast = useToast();

  return useMemo(() => {
    const boards = { queryKey: [...TASKS_KEY, "board"] };

    const run = async (guess: ((board: TaskBoard) => TaskBoard) | null, send: () => Promise<Task>): Promise<Task | null> => {
      await client.cancelQueries(boards);
      const before = client.getQueriesData<TaskBoard>(boards);
      if (guess) client.setQueriesData<TaskBoard>(boards, (board) => (board ? guess(board) : board));
      try {
        return await send();
      } catch (error) {
        if (guess) for (const [key, data] of before) client.setQueryData(key, data);
        toast.error(errorDetail(error));
        return null;
      } finally {
        void client.invalidateQueries({ queryKey: TASKS_KEY });
      }
    };

    // A success toast with an Undo button; "u" on the Tasks screen presses the latest one.
    const offer = (message: string, undo: () => void) => toast.success(message, { label: "Undo", run: offerUndo(undo) });

    const reopen = (task: Task) => run((board) => applyReopen(board, task.id), () => tasksApi.reopen(task.id));
    const restore = (task: Task) => run((board) => applyRestore(board, task.id), () => tasksApi.restore(task.id));
    const undoRound = (task: Task) => run((board) => applyUndoRound(board, task.id), () => tasksApi.undoRound(task.id));

    const complete = async (task: Task) => {
      const result = await run((board) => applyDone(board, task.id, Date.now()), () => tasksApi.done(task.id));
      if (!result) return null;
      if (result.repeat && result.can_undo_round) {
        const message =
          result.status === "open" && result.due
            ? `Kept this round of ${quoted(result.title)}. Next due ${formatDay(result.due, result.due)}.`
            : `Kept the last round of ${quoted(result.title)}.`;
        offer(message, () => void undoRound(result));
      } else if (result.status === "done") {
        offer(`Done: ${quoted(result.title)}`, () => void reopen(result));
      }
      return result;
    };

    const drop = async (task: Task) => {
      const result = await run((board) => applyDrop(board, task.id, Date.now()), () => tasksApi.drop(task.id));
      if (result?.status === "dropped") offer(`Dropped: ${quoted(result.title)}`, () => void restore(result));
      return result;
    };

    const push = async (task: Task, to: string) => {
      const result = await run((board) => applyPush(board, task.id, to), () => tasksApi.push(task.id, to));
      if (result && result.due === to) {
        // Undo takes the push itself back: the earlier date and push count return,
        // and a repeating task's rounds are counted as before.
        offer(`Pushed ${quoted(result.title)} to ${formatDay(to, to)}.`, () => void run(null, () => tasksApi.unpush(task.id)));
      }
      return result;
    };

    const rank = (task: Task, target: RankTarget) => run((board) => applyRank(board, task.id, target), () => tasksApi.rank(task.id, target));

    const refresh = () => void client.invalidateQueries({ queryKey: TASKS_KEY });

    return { complete, drop, push, rank, reopen, restore, undoRound, refresh };
  }, [client, toast]);
}
