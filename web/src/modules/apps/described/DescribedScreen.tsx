import { Plus, SearchX, Table2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { EmptyState } from "../../../components/EmptyState";
import { usePersistentState } from "../../../hooks/usePersistentState";
import { useToast } from "../../../hooks/useToast";
import type { DescribedRecord, Transport, Value, ViewSpec } from "./api";
import { BoardLayout } from "./BoardLayout";
import { CalendarLayout } from "./CalendarLayout";
import { useDescribedApi, useOwnerCalendar, useRecordActions, useRecords } from "./hooks";
import { dayOfTimestamp, gridRange, monthOf } from "./lib/calendar";
import type { Month } from "./lib/calendar";
import { LAYOUT_LABELS, calendarField, chooseLayout, layoutsFor } from "./lib/layout";
import type { Layout } from "./lib/layout";
import { readDescription, recordNoun, tableLabel } from "./lib/schema";
import { effectiveSort, nextSort } from "./lib/sort";
import { ErrorLine, Segmented } from "./parts";
import { RecordDialog } from "./RecordDialog";
import { TableLayout } from "./TableLayout";

type Props = {
  /** The app's id: its records are reached through `/api/apps/<id>/bridge/records.*`. */
  appId: string;
  /** The manifest's `tables` and `views`, as data. It is read defensively before use. */
  description: unknown;
  /** Replaces how a call reaches the server; the shell never needs it, tests do. */
  transport?: Transport;
};

type Dialog = { kind: "new"; table: string; preset: Record<string, Value> } | { kind: "edit"; table: string; record: DescribedRecord } | null;

const TABLE_PAGE = 50;
const BOARD_PAGE = 200;

function useDebounced<T>(value: T, ms: number): T {
  const [seen, setSeen] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSeen(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return seen;
}

/** A described app's screen: its views as tabs, each shown as a table, board or calendar. */
export function DescribedScreen({ appId, description: raw, transport }: Props) {
  const description = useMemo(() => readDescription(raw), [raw]);
  const api = useDescribedApi(appId, transport);
  const toast = useToast();
  const actions = useRecordActions(appId, api);
  const calendar = useOwnerCalendar();
  const [viewId, setViewId] = useState<string>(() => description.views[0]?.id ?? "");
  const view: ViewSpec | undefined = description.views.find((v) => v.id === viewId) ?? description.views[0];
  const [layouts, setLayouts] = usePersistentState<Record<string, string>>(`jimmyhq.described.${appId}.layouts`, {}, (stored) =>
    stored && typeof stored === "object" && !Array.isArray(stored) ? (stored as Record<string, string>) : undefined,
  );
  const [sorts, setSorts] = useState<Record<string, string[] | null>>({});
  const [search, setSearch] = useState("");
  const [month, setMonth] = useState<Month | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const searching = useDebounced(search.trim(), 250);
  const today = dayOfTimestamp(Date.now(), calendar.timeZone);

  const layout: Layout = view ? chooseLayout(description, view, layouts[view.id]) : "table";
  const at: Month = month ?? monthOf(today);
  const dateField = view ? calendarField(description, view) : null;
  const chosen = view ? (sorts[view.id] ?? null) : null;

  const args = useMemo(() => {
    if (!view) return null;
    const range = layout === "calendar" && dateField ? gridRange(at, calendar.weekStart) : null;
    return {
      table: view.table,
      view: view.id,
      ...(chosen ? { sort: chosen } : {}),
      ...(searching ? { search: searching } : {}),
      ...(range && dateField
        ? { filter: [{ field: dateField, op: "ge", value: range.from }, { field: dateField, op: "le", value: range.to }] }
        : {}),
      limit: layout === "table" ? TABLE_PAGE : BOARD_PAGE,
    };
  }, [view, layout, dateField, at.year, at.month, calendar.weekStart, chosen, searching]);

  const records = useRecords(appId, api, args ?? { table: "" }, args !== null);

  if (!view) {
    return <EmptyState icon={Table2} title="Nothing to show yet" description="This app does not describe any views, so there is nothing to draw." />;
  }
  const table = description.tables[view.table];
  if (!table) return null;
  const noun = recordNoun(view.table);
  const options = layoutsFor(description, view);
  const data = records.data;
  const empty = data !== null && data.total === 0;

  const open = (record: DescribedRecord) => setDialog({ kind: "edit", table: view.table, record });
  const add = (preset: Record<string, Value> = {}) => setDialog({ kind: "new", table: view.table, preset });
  const move = async (record: DescribedRecord, field: string, to: Value) => {
    const outcome = await actions.update(view.table, record.id, { [field]: to });
    if (!outcome.ok) toast.error(outcome.message);
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      {description.views.length > 1 ? (
        <div role="tablist" aria-label="Views" className="flex flex-wrap gap-1 border-b border-border">
          {description.views.map((v) => {
            const selected = v.id === view.id;
            return (
              <button
                key={v.id}
                id={`described-tab-${v.id}`}
                role="tab"
                type="button"
                aria-selected={selected}
                aria-controls="described-panel"
                onClick={() => {
                  setViewId(v.id);
                  setSearch("");
                }}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${selected ? "border-accent font-semibold text-text" : "border-transparent text-text-muted hover:text-text"}`}
              >
                {v.label}
              </button>
            );
          })}
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        {options.length > 1 ? <Segmented label="Layout" value={layout} options={options.map((l) => ({ value: l, label: LAYOUT_LABELS[l] }))} onChange={(l) => setLayouts((all) => ({ ...all, [view.id]: l }))} /> : null}
        <input type="search" className="input h-8 max-w-xs flex-1 text-xs" placeholder={`Search ${tableLabel(view.table, table).toLowerCase()}`} aria-label={`Search ${tableLabel(view.table, table).toLowerCase()}`} maxLength={200} value={search} onChange={(e) => setSearch(e.target.value)} />
        <span className="ml-auto text-xs text-text-faint" aria-live="polite">
          {data ? `${data.total} ${data.total === 1 ? noun : `${noun}s`}` : ""}
        </span>
        <button type="button" className="btn btn-primary h-8" onClick={() => add()}>
          <Plus size={14} aria-hidden="true" /> Add {noun}
        </button>
      </div>

      <div id="described-panel" role="tabpanel" aria-labelledby={description.views.length > 1 ? `described-tab-${view.id}` : undefined} className="min-h-0">
        {records.error ? <ErrorLine>{records.error}</ErrorLine> : null}
        {records.loading && !data ? <p className="py-8 text-center text-sm text-text-muted">Loading…</p> : null}
        {data && empty && layout !== "calendar" ? (
          <EmptyState
            icon={searching ? SearchX : Table2}
            title={searching ? `No ${noun} matches “${searching.length > 40 ? `${searching.slice(0, 39)}…` : searching}”` : `No ${tableLabel(view.table, table).toLowerCase()} yet`}
            description={searching ? "Try a shorter search, or clear it to see everything." : `Add the first ${noun} and it will show here.`}
            compact
          >
            {searching ? (
              <button type="button" className="btn" onClick={() => setSearch("")}>
                Clear the search
              </button>
            ) : (
              <button type="button" className="btn btn-primary" onClick={() => add()}>
                Add {noun}
              </button>
            )}
          </EmptyState>
        ) : null}
        {data && !(empty && layout !== "calendar") ? (
          <>
            {layout === "table" ? <TableLayout description={description} view={view} data={data} sort={effectiveSort(chosen, view.sort)} onSort={(field) => setSorts((all) => ({ ...all, [view.id]: nextSort(chosen, view.sort, field) }))} onOpen={open} /> : null}
            {layout === "board" ? <BoardLayout description={description} view={view} data={data} onOpen={open} onMove={move} onAdd={(field, to) => add({ [field]: to })} /> : null}
            {layout === "calendar" ? <CalendarLayout description={description} view={view} data={data} month={at} weekStart={calendar.weekStart} today={today} onMonth={setMonth} onOpen={open} onAdd={(field, day) => add({ [field]: day })} /> : null}
            {data.hasMore ? (
              <div className="flex items-center justify-center gap-3 py-3 text-xs text-text-muted">
                <span>
                  Showing {data.records.length} of {data.total}
                </span>
                <button type="button" className="btn h-8" onClick={records.loadMore} disabled={records.loadingMore}>
                  {records.loadingMore ? "Loading…" : "Show more"}
                </button>
              </div>
            ) : null}
          </>
        ) : null}
      </div>

      {dialog ? (
        <RecordDialog
          appId={appId}
          api={api}
          description={description}
          table={dialog.table}
          record={dialog.kind === "edit" ? dialog.record : undefined}
          preset={dialog.kind === "new" ? dialog.preset : undefined}
          labels={data?.labels ?? {}}
          timeZone={calendar.timeZone}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  );
}
