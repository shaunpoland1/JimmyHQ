import { ListTodo, Plus, SearchX } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { errorDetail } from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { usePersistentState } from "../../hooks/usePersistentState";
import { useAgentNames } from "../../lib/useAgentNames";
import type { BoardFilter, Task, TaskBoard } from "./api";
import { OpenList } from "./components/OpenList";
import { BoardView, FinishedList } from "./components/OtherViews";
import { PushDialog, TaskDialog } from "./components/TaskDialog";
import { keyContextOf } from "./components/keyContext";
import { Segmented } from "./components/parts";
import { NO_FILTER, OWNER_FILTER, useBoard, useTaskActions } from "./hooks";
import { earliestPush, pushOneDayTarget } from "./lib/dates";
import { agentTasksHidden, assigneeLabel, assigneesOn, narrowsBeyondDefault, searchTasks, withAgentsDone } from "./lib/group";
import { KEY_HINTS, listAction } from "./lib/keys";
import { undoLatest } from "./lib/undo";

type View = "list" | "board";
type Show = "open" | "done";
type Dialog = { kind: "new" } | { kind: "edit"; task: Task } | { kind: "push"; task: Task } | null;

function parseView(stored: unknown): View | undefined {
  return stored === "list" || stored === "board" ? stored : undefined;
}

const SELECT = "input h-7 w-auto max-w-[11rem] pl-2 pr-7 text-xs";

/** The Tasks screen: one list for the owner and the agents. */
export function TasksView() {
  const [view, setView] = usePersistentState<View>("jimmyhq.tasks.view", "list", parseView);
  const [show, setShow] = useState<Show>("open");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<BoardFilter>(OWNER_FILTER);
  const [dialog, setDialog] = useState<Dialog>(null);
  const names = useAgentNames();
  const filtered = narrowsBeyondDefault(filter);

  const query = useBoard(filter);
  // The unfiltered board (shared with the Today panel) knows every area and assignee.
  const everything = useBoard(NO_FILTER);
  const actions = useTaskActions();
  const board = query.data;

  // "n" and "u" work on every view of the screen, including an empty list.
  const root = useRef<HTMLDivElement>(null);
  const ready = useRef(false);
  ready.current = board !== undefined;
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const context = keyContextOf(event, root.current);
      if (!context) return;
      const action = listAction(event, context);
      if (action === "new" && ready.current) {
        event.preventDefault();
        setDialog({ kind: "new" });
      } else if (action === "undo" && undoLatest()) {
        event.preventDefault();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const known = useMemo(
    () => (board ? assigneesOn(everything.data ?? board, filter.assignee ? [filter.assignee] : []) : ["owner"]),
    [board, everything.data, filter.assignee],
  );

  const hiddenAgents = agentTasksHidden(everything.data, filter);

  const shown = useMemo<TaskBoard | undefined>(() => {
    if (!board) return undefined;
    if (!search.trim()) return board;
    return { ...board, open: searchTasks(board.open, search), done: searchTasks(board.done, search), dropped: searchTasks(board.dropped, search) };
  }, [board, search]);

  const header = (
    <div className="mb-4 flex flex-wrap items-center gap-x-2.5 gap-y-2">
      <h1 className="text-[17px] font-semibold tracking-[-0.01em]">Tasks</h1>
      <span className="hidden text-text-muted @2xl:inline">one list for you and the agents</span>
      <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-1.5">
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: "list", label: "List" },
            { value: "board", label: "Board" },
          ]}
        />
        {view === "list" ? (
          <Segmented
            label="Which tasks"
            value={show}
            onChange={setShow}
            options={[
              { value: "open", label: "open", count: board ? board.counts.open.toLocaleString() : undefined },
              { value: "done", label: "done", count: board ? (board.done.length + board.dropped.length).toLocaleString() : undefined },
            ]}
          />
        ) : null}
        <input
          type="search"
          className="input h-7 w-28 px-2.5 @md:w-[150px]"
          placeholder="search"
          aria-label="Search tasks by title or area"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <button type="button" className="btn btn-primary h-7 px-2.5" onClick={() => setDialog({ kind: "new" })} disabled={!board}>
          <Plus size={14} aria-hidden="true" />
          Task
        </button>
      </div>
    </div>
  );

  let body;
  if (!board || !shown) {
    body = query.isError ? (
      <EmptyState icon={ListTodo} title="Tasks unavailable" description={errorDetail(query.error)}>
        <button type="button" className="btn" onClick={() => void query.refetch()}>
          Try again
        </button>
      </EmptyState>
    ) : (
      <p className="py-6 text-sm text-text-faint">Loading tasks…</p>
    );
  } else {
    const areas = (everything.data ?? board).areas;
    const areaChoices = filter.area && !areas.includes(filter.area) ? [...areas, filter.area] : areas;
    const searching = search.trim() !== "";
    const nothing = (title: string, description: string, canAdd: boolean) => (
      <div className="py-10">
        <EmptyState icon={searching || filtered ? SearchX : ListTodo} title={title} description={description}>
          {canAdd ? (
            <button type="button" className="btn btn-primary" onClick={() => setDialog({ kind: "new" })}>
              <Plus size={14} aria-hidden="true" />
              New task
            </button>
          ) : null}
        </EmptyState>
      </div>
    );
    const narrowed = searching || filtered;

    let content;
    if (view === "board") {
      content = <BoardView board={withAgentsDone(shown, everything.data, filter)} known={known} onEdit={(task) => setDialog({ kind: "edit", task })} />;
    } else if (show === "done") {
      content =
        shown.done.length + shown.dropped.length === 0 ? (
          nothing(
            narrowed ? "No finished tasks match" : "Nothing finished lately",
            narrowed ? "Try another search, or clear the filters." : `Tasks you finish or drop stay here for ${shown.done_days === 1 ? "a day" : `${shown.done_days} days`}, in case you want one back.`,
            false,
          )
        ) : (
          <FinishedList done={shown.done} dropped={shown.dropped} days={shown.done_days} known={known} actions={actions} />
        );
    } else if (shown.open.length === 0) {
      content = nothing(
        narrowed ? "No open tasks match" : "Nothing on the list",
        narrowed ? "Try another search, or clear the filters." : "Add the first thing you want to get done. Press n to start.",
        !narrowed,
      );
    } else {
      content = (
        <OpenList
          board={shown}
          tasks={shown.open}
          known={known}
          actions={actions}
          onEdit={(task) => setDialog({ kind: "edit", task })}
          onPickDate={(task) => setDialog({ kind: "push", task })}
        />
      );
    }

    body = (
      <>
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <select
            className={SELECT}
            aria-label="Filter by area"
            value={filter.area ?? ""}
            onChange={(event) => setFilter((current) => ({ ...current, area: event.target.value || null }))}
          >
            <option value="">All areas</option>
            {areaChoices.map((area) => (
              <option key={area} value={area}>
                {area}
              </option>
            ))}
          </select>
          <select
            className={SELECT}
            aria-label="Filter by who it is assigned to"
            value={filter.assignee ?? ""}
            onChange={(event) => setFilter((current) => ({ ...current, assignee: event.target.value || null }))}
          >
            <option value="">Everyone</option>
            {known.map((person) => (
              <option key={person} value={person}>
                {assigneeLabel(person, names)}
              </option>
            ))}
          </select>
          {narrowed ? (
            <button
              type="button"
              className="btn h-7 px-2 text-xs"
              onClick={() => {
                setFilter(OWNER_FILTER);
                setSearch("");
              }}
            >
              Clear
            </button>
          ) : null}
          {query.isError ? (
            <span role="alert" className="flex min-w-0 items-center gap-1.5 text-xs text-danger">
              <span className="min-w-0 break-words">Could not refresh: {errorDetail(query.error)}</span>
              <button type="button" className="btn h-7 flex-none px-2 text-xs" onClick={() => void query.refetch()}>
                Try again
              </button>
            </span>
          ) : null}
        </div>
        {hiddenAgents > 0 ? (
          <p className="mb-3 text-xs text-text-muted" role="status">
            {hiddenAgents === 1 ? "1 open task belongs to one of your agents and is not shown here." : `${hiddenAgents} open tasks belong to your agents and are not shown here.`}{" "}
            <button type="button" className="underline underline-offset-2" onClick={() => setFilter(NO_FILTER)}>
              Show everyone's
            </button>
          </p>
        ) : null}
        <div className={query.isPlaceholderData ? "opacity-60" : undefined} aria-busy={query.isPlaceholderData || undefined}>
          {content}
        </div>
        {view === "list" && show === "open" && shown.open.length > 0 ? (
          <dl className="mt-[18px] hidden flex-wrap gap-x-3 gap-y-1 border-t border-border pt-3 font-mono text-[11px] text-text-faint @2xl:flex" aria-label="Keyboard shortcuts">
            {KEY_HINTS.map((hint) => (
              <div key={hint.keys} className="flex gap-1.5">
                <dt className="text-text-muted">{hint.keys}</dt>
                <dd>{hint.label}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </>
    );
  }

  return (
    <div ref={root} className="@container w-full">
      <div className="max-w-[1120px] px-3.5 pb-8 pt-4 @2xl:px-[26px] @2xl:pb-10 @2xl:pt-[22px]">
        {header}
        {body}
      </div>
      {board && dialog?.kind === "new" ? (
        <TaskDialog task={null} board={everything.data ?? board} assignees={known} onClose={() => setDialog(null)} onSaved={actions.refresh} />
      ) : null}
      {board && dialog?.kind === "edit" ? (
        <TaskDialog task={dialog.task} board={everything.data ?? board} assignees={known} onClose={() => setDialog(null)} onSaved={actions.refresh} />
      ) : null}
      {board && dialog?.kind === "push" ? (
        <PushDialog
          task={dialog.task}
          min={earliestPush(dialog.task.due, board.date)}
          initial={pushOneDayTarget(dialog.task.due, board.date)}
          onClose={() => setDialog(null)}
          onPush={(to) => void actions.push(dialog.task, to)}
        />
      ) : null}
    </div>
  );
}
