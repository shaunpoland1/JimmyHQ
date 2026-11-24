import { ArrowRight, CalendarDays, Check, ChevronDown, ChevronUp, GripVertical, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import type { Task, TaskBoard } from "../api";
import type { TaskActions } from "../hooks";
import { addDays, formatClock, formatDay, formatLongDay, lateText, pushOneDayTarget } from "../lib/dates";
import { groupOpen, roundKeptToday } from "../lib/group";
import type { GroupId, TaskGroup } from "../lib/group";
import { listAction, selectionAfterRemoval, stepSelection } from "../lib/keys";
import { dropTarget, stepTarget } from "../lib/optimistic";
import { keyContextOf } from "./keyContext";
import { AssigneeTile, Pill, Tag } from "./parts";

type OpenListProps = {
  board: TaskBoard;
  /** The open tasks to show (already searched), in manual order. */
  tasks: readonly Task[];
  /** Known assignees, for tile colours. */
  known: readonly string[];
  actions: TaskActions;
  onEdit: (task: Task) => void;
  onPickDate: (task: Task) => void;
};

type Drag = { id: string; group: GroupId; over: string | null; side: "before" | "after" };

function groupDate(id: GroupId, today: string): string | null {
  if (id === "today") return formatLongDay(today);
  if (id === "tomorrow") return formatLongDay(addDays(today, 1));
  return null;
}

/** The open tasks in their groups, with keyboard control and drag to reorder. */
export function OpenList({ board, tasks, known, actions, onEdit, onPickDate }: OpenListProps) {
  const groups = useMemo(() => groupOpen(tasks, board.date), [tasks, board.date]);
  const ordered = useMemo(() => groups.flatMap((group) => group.tasks), [groups]);
  const ids = useMemo(() => ordered.map((task) => task.id), [ordered]);
  const [selected, setSelected] = useState<string | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const root = useRef<HTMLDivElement>(null);
  // True while the keyboard is steering, so focus follows the selection.
  const steering = useRef(false);

  const active = selected !== null && ids.includes(selected) ? selected : (ids[0] ?? null);

  // Everything the key handler needs, without re-binding it on every render.
  const live = useRef({ ids, ordered, groups, selected, board, actions, onEdit });
  live.current = { ids, ordered, groups, selected, board, actions, onEdit };

  const tick = useCallback((task: Task) => {
    const { ids: current, actions: act } = live.current;
    setSelected((was) => (was === task.id ? selectionAfterRemoval(current, task.id) : was));
    void act.complete(task);
  }, []);

  const move = useCallback((task: Task, direction: -1 | 1) => {
    const group = live.current.groups.find((item) => item.tasks.some((row) => row.id === task.id));
    if (!group) return;
    const target = stepTarget(
      group.tasks.map((row) => row.id),
      task.id,
      direction,
    );
    if (target) void live.current.actions.rank(task, target);
  }, []);

  const pushDay = useCallback((task: Task) => {
    void live.current.actions.push(task, pushOneDayTarget(task.due, live.current.board.date));
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const context = keyContextOf(event, root.current);
      if (!context) return;
      const action = listAction(event, context);
      // New and undo belong to the screen, which answers them even when no list is shown.
      if (!action || action === "new" || action === "undo") return;

      const state = live.current;
      const current = state.ordered.find((task) => task.id === state.selected) ?? null;
      if (action === "next" || action === "prev") {
        if (state.ids.length === 0) return;
        event.preventDefault();
        steering.current = true;
        setSelected(stepSelection(state.ids, current?.id ?? null, action === "next" ? 1 : -1));
        return;
      }
      if (!current) return;
      event.preventDefault();
      steering.current = true;
      if (action === "tick") tick(current);
      else if (action === "edit" || action === "open") state.onEdit(current);
      else if (action === "push") pushDay(current);
      else if (action === "moveUp") move(current, -1);
      else if (action === "moveDown") move(current, 1);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [tick, move, pushDay]);

  // While the keyboard steers, keep focus on the selected row as rows move.
  const order = ids.join(" ");
  useEffect(() => {
    if (!steering.current || selected === null) return;
    if (document.querySelector('[role="dialog"]')) return;
    const row = root.current?.querySelector<HTMLElement>(`[data-task-row="${selected}"]`);
    if (row && !row.contains(document.activeElement)) row.focus();
    row?.scrollIntoView({ block: "nearest" });
  }, [selected, order]);

  // ---- drag to reorder (pointer only; the keyboard has move up / move down) ----
  const onGripDown = (event: ReactPointerEvent<HTMLElement>, task: Task, group: GroupId) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    steering.current = false;
    setDrag({ id: task.id, group, over: null, side: "before" });
  };
  const onGripMove = (event: ReactPointerEvent<HTMLElement>) => {
    if (!drag) return;
    const row = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-task-row]");
    if (!row || row.dataset.group !== drag.group || !root.current?.contains(row)) {
      if (drag.over !== null) setDrag({ ...drag, over: null });
      return;
    }
    const box = row.getBoundingClientRect();
    const side = event.clientY < box.top + box.height / 2 ? "before" : "after";
    const over = row.dataset.taskRow ?? null;
    if (over !== drag.over || side !== drag.side) setDrag({ ...drag, over, side });
  };
  const onGripUp = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (!drag) return;
    setDrag(null);
    if (drag.over === null) return;
    const group = groups.find((item) => item.id === drag.group);
    const task = group?.tasks.find((row) => row.id === drag.id);
    if (!group || !task) return;
    const target = dropTarget(
      group.tasks.map((row) => row.id),
      drag.id,
      drag.over,
      drag.side,
    );
    if (target) void actions.rank(task, target);
  };
  const onGripCancel = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    setDrag(null);
  };
  useEffect(() => {
    if (!drag) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrag(null);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [drag]);

  return (
    <div
      ref={root}
      onPointerDown={() => {
        steering.current = false;
      }}
      onKeyDown={() => {
        steering.current = true;
      }}
    >
      {groups.map((group) => (
        <Group
          key={group.id}
          group={group}
          today={board.date}
          render={(task, index) => (
            <Row
              key={task.id}
              task={task}
              group={group.id}
              today={board.date}
              known={known}
              selected={task.id === selected}
              tabbable={task.id === active}
              first={index === 0}
              last={index === group.tasks.length - 1}
              dragging={drag?.id === task.id}
              drop={drag && drag.over === task.id && drag.id !== task.id ? drag.side : null}
              onSelect={() => setSelected(task.id)}
              onTick={() => tick(task)}
              onPushDay={() => pushDay(task)}
              onPickDate={() => onPickDate(task)}
              onEdit={() => onEdit(task)}
              onMove={(direction) => move(task, direction)}
              onDrop={() => {
                setSelected((was) => (was === task.id ? selectionAfterRemoval(ids, task.id) : was));
                void actions.drop(task);
              }}
              grip={{
                onPointerDown: (event) => onGripDown(event, task, group.id),
                onPointerMove: onGripMove,
                onPointerUp: onGripUp,
                onPointerCancel: onGripCancel,
              }}
            />
          )}
        />
      ))}
    </div>
  );
}

function Group({ group, today, render }: { group: TaskGroup; today: string; render: (task: Task, index: number) => ReactNode }) {
  const date = groupDate(group.id, today);
  const headingId = `tasks-group-${group.id}`;
  return (
    <section aria-labelledby={headingId} className="mb-[18px]">
      <h2 id={headingId} className="flex items-center gap-2 px-0.5 pb-1.5 font-mono text-[11px] font-normal tracking-[0.04em] text-text-faint after:h-px after:flex-1 after:bg-border after:content-['']">
        <b className={`font-semibold uppercase tracking-[0.1em] ${group.id === "today" ? "text-accent" : "text-text"}`}>{group.label}</b>
        {date ? <span className="min-w-0 truncate">· {date}</span> : null}
        <span aria-label={`${group.tasks.length} ${group.tasks.length === 1 ? "task" : "tasks"}`}>· {group.tasks.length}</span>
      </h2>
      <div role="list" className="rounded-[7px] border border-border bg-surface">
        {group.tasks.map(render)}
      </div>
    </section>
  );
}

type GripHandlers = {
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerMove: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerUp: (event: ReactPointerEvent<HTMLElement>) => void;
  onPointerCancel: (event: ReactPointerEvent<HTMLElement>) => void;
};

type RowProps = {
  task: Task;
  group: GroupId;
  today: string;
  known: readonly string[];
  selected: boolean;
  /** Only one row's controls are in the Tab order; the arrows move between rows. */
  tabbable: boolean;
  first: boolean;
  last: boolean;
  dragging: boolean;
  drop: "before" | "after" | null;
  onSelect: () => void;
  onTick: () => void;
  onPushDay: () => void;
  onPickDate: () => void;
  onEdit: () => void;
  onMove: (direction: -1 | 1) => void;
  onDrop: () => void;
  grip: GripHandlers;
};

const META = "whitespace-nowrap font-mono text-[11px] text-text-faint";
const ACT = "icon-btn size-6 aria-disabled:cursor-not-allowed aria-disabled:opacity-40";

function Row({ task, group, today, known, selected, tabbable, first, last, dragging, drop, onSelect, onTick, onPushDay, onPickDate, onEdit, onMove, onDrop, grip }: RowProps) {
  const tab = tabbable ? 0 : -1;
  // A round just kept, shown ticked until the server says when the next one is due.
  const kept = roundKeptToday(task, today);
  const pushLabel = !task.due || task.due <= today ? "Push to tomorrow" : "Push a day";
  return (
    <div
      role="listitem"
      data-task-row={task.id}
      data-group={group}
      data-selected={selected}
      data-dragging={dragging || undefined}
      data-drop={drop ?? undefined}
      tabIndex={tab}
      onFocus={onSelect}
      onClick={(event) => {
        if (!(event.target instanceof Element && event.target.closest("button"))) onSelect();
      }}
      onDoubleClick={(event) => {
        if (!(event.target instanceof Element && event.target.closest("button"))) onEdit();
      }}
      className={`group relative flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-border/55 px-2.5 py-1.5 hover:bg-surface-2/55 focus-visible:-outline-offset-2 data-[dragging=true]:opacity-50 data-[drop=after]:shadow-[inset_0_-2px_0_var(--accent)] data-[drop=before]:shadow-[inset_0_2px_0_var(--accent)] data-[selected=true]:bg-surface-2 ${
        first ? "rounded-t-[6px]" : ""
      } ${last ? "rounded-b-[6px] border-b-0" : ""}`}
    >
      <span
        aria-hidden="true"
        title="Drag to reorder"
        {...grip}
        className="-ml-1.5 -mr-1.5 hidden h-6 w-4 flex-none cursor-grab touch-none place-items-center text-text-faint opacity-0 active:cursor-grabbing group-hover:opacity-100 @xl:grid"
      >
        <GripVertical size={13} />
      </span>
      <button
        type="button"
        tabIndex={tab}
        onClick={onTick}
        aria-label={task.repeat ? `Keep this round of ${task.title}` : `Mark ${task.title} done`}
        title={task.repeat ? "Keep this round" : "Done"}
        className="group/chk -m-1 grid size-6 flex-none place-items-center rounded-full"
      >
        <span
          className={`grid size-4 place-items-center rounded-full border-[1.5px] ${
            kept ? "border-success bg-success text-accent-fg" : "border-sage text-transparent group-hover/chk:border-accent group-hover/chk:text-accent"
          }`}
        >
          <Check size={10} strokeWidth={3} aria-hidden="true" />
        </span>
      </button>
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-[7px] gap-y-0.5">
        <span className={`min-w-0 break-words ${kept ? "text-text-faint line-through" : ""}`}>{task.title}</span>
        {task.due_time ? <span className={META}>{formatClock(task.due_time)}</span> : null}
        {task.repeat_text ? <span className={`${META} max-w-full truncate`}>· {task.repeat_text}</span> : null}
        {task.due ? (
          <span className={META}>
            → <time dateTime={task.due}>{formatDay(task.due, today)}</time>
          </span>
        ) : null}
        {task.lapsed_days > 0 ? <span className="whitespace-nowrap font-mono text-[11px] text-danger">{lateText(task.lapsed_days)}</span> : null}
        {task.stalled ? (
          <Pill tone="warn" title={`Pushed ${task.pushes} times without being done`}>
            stalled {task.pushes}×
          </Pill>
        ) : null}
        {task.area ? <Tag className="inline-block self-center @xl:hidden">{task.area}</Tag> : null}
      </div>
      {task.area ? <Tag className="hidden @xl:inline-block">{task.area}</Tag> : null}
      <AssigneeTile assignee={task.assignee} known={known} />
      <div className="hidden basis-full justify-end gap-px group-focus-within:flex group-data-[selected=true]:flex @xl:flex @xl:basis-auto @xl:opacity-55 @xl:group-focus-within:opacity-100 @xl:group-hover:opacity-100 @xl:group-data-[selected=true]:opacity-100">
        <button type="button" tabIndex={tab} className={ACT} onClick={onPushDay} title={pushLabel} aria-label={`${pushLabel}: ${task.title}`}>
          <ArrowRight size={14} />
        </button>
        <button type="button" tabIndex={tab} className={ACT} onClick={onPickDate} title="Push to a date" aria-label={`Push to a date: ${task.title}`}>
          <CalendarDays size={14} />
        </button>
        <button type="button" tabIndex={tab} className={ACT} onClick={onEdit} title="Edit" aria-label={`Edit ${task.title}`}>
          <Pencil size={13} />
        </button>
        <button type="button" tabIndex={tab} className={ACT} onClick={() => onMove(-1)} aria-disabled={first || undefined} title="Move up" aria-label={`Move up: ${task.title}`}>
          <ChevronUp size={14} />
        </button>
        <button type="button" tabIndex={tab} className={ACT} onClick={() => onMove(1)} aria-disabled={last || undefined} title="Move down" aria-label={`Move down: ${task.title}`}>
          <ChevronDown size={14} />
        </button>
        <button type="button" tabIndex={tab} className={`${ACT} hover:text-danger!`} onClick={onDrop} title="Drop" aria-label={`Drop ${task.title}`}>
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
