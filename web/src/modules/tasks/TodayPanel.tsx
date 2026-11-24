import { CalendarDays, Check } from "lucide-react";
import { useMemo } from "react";
import { errorDetail } from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { Pill, Tag } from "./components/parts";
import { OWNER_FILTER, useBoard, useTaskActions } from "./hooks";
import { formatClock, formatShortDay, lateText } from "./lib/dates";
import { planCounts, todayList } from "./lib/group";
import type { TodayItem } from "./lib/group";

/** The Today panel in the right dock. Owned by the tasks module. */
export function TodayPanel() {
  // The same query as the Tasks screen's own-tasks board, so the two stay in step. An agent's chores are not on the owner's Today.
  const query = useBoard(OWNER_FILTER);
  const actions = useTaskActions();
  const board = query.data;
  const list = useMemo(() => (board ? todayList(board) : null), [board]);
  const plan = useMemo(() => (board ? planCounts(board) : null), [board]);

  if (!board || !list || !plan) {
    if (query.isError) {
      return (
        <EmptyState icon={CalendarDays} title="Today is unavailable" description={errorDetail(query.error)} compact>
          <button type="button" className="btn" onClick={() => void query.refetch()}>
            Try again
          </button>
        </EmptyState>
      );
    }
    return <p className="p-4 text-xs text-text-faint">Loading today…</p>;
  }

  const press = (item: TodayItem) => {
    if (item.state === "due") void actions.complete(item.task);
    else if (item.state === "kept") void actions.undoRound(item.task);
    else void actions.reopen(item.task);
  };

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="flex flex-none justify-between gap-2 px-3 pb-1 pt-3 font-mono text-[10.5px] uppercase tracking-[0.1em] text-text-faint">
        <time dateTime={board.date}>{formatShortDay(board.date)}</time>
        <span aria-live="polite">
          {list.done} / {list.total} done
        </span>
      </div>
      {query.isError ? (
        <p role="alert" className="px-3 py-1 text-xs text-danger">
          Could not refresh: {errorDetail(query.error)}{" "}
          <button type="button" className="underline" onClick={() => void query.refetch()}>
            Try again
          </button>
        </p>
      ) : null}
      {list.items.length === 0 ? (
        <p className="px-3 py-3 text-xs text-text-muted">Nothing is due today.</p>
      ) : (
        <ul aria-label="Today's tasks">
          {list.items.map((item) => {
            const { task } = item;
            const ticked = item.state !== "due";
            return (
              <li key={task.id}>
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={ticked}
                  aria-label={ticked ? `${task.title}, done. Press to undo.` : task.repeat ? `Keep this round of ${task.title}` : `Mark ${task.title} done`}
                  title={ticked ? "Undo" : undefined}
                  onClick={() => press(item)}
                  className="flex w-full items-center gap-2 px-3 py-[5px] text-left hover:bg-surface-2/60 focus-visible:-outline-offset-2"
                >
                  <span
                    aria-hidden="true"
                    className={`grid size-3.5 flex-none place-items-center rounded border-[1.5px] ${
                      ticked ? "border-success bg-success text-accent-fg" : "border-sage text-transparent"
                    }`}
                  >
                    <Check size={10} strokeWidth={3} />
                  </span>
                  <span className={`min-w-0 flex-1 truncate ${ticked ? "text-text-faint line-through" : ""}`}>
                    {task.title}
                    {!ticked && task.due_time ? <span className="text-text-muted"> · {formatClock(task.due_time)}</span> : null}
                  </span>
                  {!ticked && task.lapsed_days > 0 ? <span className="flex-none whitespace-nowrap font-mono text-[10.5px] text-danger">{lateText(task.lapsed_days)}</span> : null}
                  {task.area ? <Tag className="inline-block max-w-[5.5rem]!">{task.area}</Tag> : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-2 flex flex-none flex-wrap items-center gap-1.5 border-t border-border px-3 py-2.5">
        <span className="font-mono text-[10.5px] uppercase tracking-[0.1em] text-text-faint">The plan</span>
        <Pill tone="accent">now {plan.now}</Pill>
        <Pill>next {plan.next}</Pill>
        <Pill>later {plan.later}</Pill>
      </div>
    </div>
  );
}
