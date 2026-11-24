import { Check, RotateCcw } from "lucide-react";
import type { Task, TaskBoard } from "../api";
import type { TaskActions } from "../hooks";
import { formatAgo, formatDay } from "../lib/dates";
import { boardColumns, finishedRangeText } from "../lib/group";
import { AssigneeTile, Pill, Tag } from "./parts";

const META = "whitespace-nowrap font-mono text-[11px] text-text-faint";

type FinishedProps = { done: readonly Task[]; dropped: readonly Task[]; days: number; known: readonly string[]; actions: TaskActions };

/** Finished and dropped tasks, newest first, each with a way back. */
export function FinishedList({ done, dropped, days, known, actions }: FinishedProps) {
  const now = Date.now();
  return (
    <div>
      <p className="mb-3 px-0.5 text-xs text-text-faint">{finishedRangeText(days)} Older ones stay on the record.</p>
      <FinishedGroup id="done" label="Done" tasks={done} known={known} now={now} onBack={(task) => void actions.reopen(task)} backLabel="Reopen" />
      <FinishedGroup id="dropped" label="Dropped" tasks={dropped} known={known} now={now} onBack={(task) => void actions.restore(task)} backLabel="Restore" />
    </div>
  );
}

type FinishedGroupProps = {
  id: "done" | "dropped";
  label: string;
  tasks: readonly Task[];
  known: readonly string[];
  now: number;
  backLabel: string;
  onBack: (task: Task) => void;
};

function FinishedGroup({ id, label, tasks, known, now, backLabel, onBack }: FinishedGroupProps) {
  if (tasks.length === 0) return null;
  const headingId = `tasks-group-${id}`;
  return (
    <section aria-labelledby={headingId} className="mb-[18px]">
      <h2 id={headingId} className="flex items-center gap-2 px-0.5 pb-1.5 font-mono text-[11px] font-normal tracking-[0.04em] text-text-faint after:h-px after:flex-1 after:bg-border after:content-['']">
        <b className="font-semibold uppercase tracking-[0.1em] text-text">{label}</b>
        <span>· {tasks.length}</span>
      </h2>
      <ul className="rounded-[7px] border border-border bg-surface">
        {tasks.map((task) => {
          const at = id === "done" ? task.done_at : task.dropped_at;
          return (
            <li key={task.id} className="flex items-center gap-2.5 border-b border-border/55 px-2.5 py-1.5 last:border-b-0">
              <span
                aria-hidden="true"
                className={`grid size-4 flex-none place-items-center rounded-full border-[1.5px] ${
                  id === "done" ? "border-success bg-success text-accent-fg" : "border-sage text-transparent"
                }`}
              >
                <Check size={10} strokeWidth={3} />
              </span>
              <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-[7px] gap-y-0.5">
                <span className="min-w-0 break-words text-text-faint line-through">{task.title}</span>
                {task.repeat_text ? <span className={`${META} max-w-full truncate`}>· {task.repeat_text}</span> : null}
                {at !== null ? (
                  <time dateTime={new Date(at).toISOString()} className={META}>
                    {id} {formatAgo(at, now)}
                  </time>
                ) : null}
              </div>
              {task.area ? <Tag className="hidden @xl:inline-block">{task.area}</Tag> : null}
              <AssigneeTile assignee={task.assignee} known={known} />
              <button type="button" className="icon-btn size-6" onClick={() => onBack(task)} title={backLabel} aria-label={`${backLabel} ${task.title}`}>
                <RotateCcw size={13} />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

type BoardViewProps = { board: TaskBoard; known: readonly string[]; onEdit: (task: Task) => void };

/** Now, Next, Later and Done for you, as simple cards. */
export function BoardView({ board, known, onEdit }: BoardViewProps) {
  const columns = boardColumns(board);
  const now = Date.now();
  return (
    <div className="grid grid-cols-1 items-start gap-3 @xl:grid-cols-2 @5xl:grid-cols-4">
      {columns.map((column) => {
        const headingId = `tasks-column-${column.id}`;
        return (
          <section key={column.id} aria-labelledby={headingId} className="min-w-0 rounded-[7px] border border-border bg-surface/50 p-2">
            <h2 id={headingId} className="flex items-center gap-1.5 px-1 pb-2 pt-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em] text-text-muted">
              {column.label}
              <span className="font-normal text-text-faint">{column.tasks.length}</span>
            </h2>
            {column.tasks.length === 0 ? <p className="px-1 pb-1 text-xs text-text-faint">Nothing here.</p> : null}
            <ul className="flex flex-col gap-1.5">
              {column.tasks.map((task) =>
                column.id === "done_for_you" ? (
                  <li key={task.id} className="rounded-md border border-border bg-surface px-2.5 py-2">
                    <div className="break-words text-text-muted">{task.title}</div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <Pill tone="ok">done</Pill>
                      {task.done_at !== null ? <span className={META}>{formatAgo(task.done_at, now)}</span> : null}
                      <span className="ml-auto">
                        <AssigneeTile assignee={task.assignee} known={known} />
                      </span>
                    </div>
                  </li>
                ) : (
                  <li key={task.id}>
                    <button
                      type="button"
                      onClick={() => onEdit(task)}
                      aria-label={`Edit ${task.title}`}
                      className="block w-full rounded-md border border-border bg-surface px-2.5 py-2 text-left hover:border-sage"
                    >
                      <span className="block break-words">{task.title}</span>
                      <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {task.area ? <Tag>{task.area}</Tag> : null}
                        {task.due ? (
                          <span className={`${META} ${task.lapsed_days > 0 ? "text-danger!" : ""}`}>
                            <time dateTime={task.due}>{formatDay(task.due, board.date)}</time>
                          </span>
                        ) : null}
                        {task.stalled ? <Pill tone="warn">stalled {task.pushes}×</Pill> : null}
                        <span className="ml-auto">
                          <AssigneeTile assignee={task.assignee} known={known} />
                        </span>
                      </span>
                    </button>
                  </li>
                ),
              )}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
