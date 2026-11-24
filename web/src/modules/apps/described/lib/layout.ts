// Which ways a view can be laid out, and how its records fall into groups. The manifest
// gives a view a table, filter, sort, group and totals but no "kind", so the layouts are
// worked out from what the view and its table have: a table always; a board when the view
// groups by a choice field; a calendar when the table has a date field.

import type { Description, DescribedRecord, FieldSpec, GroupSummary, TableSpec, Value, ViewSpec } from "../api.ts";
import { fieldLabel, viewFields } from "./schema.ts";

export type Layout = "table" | "board" | "calendar";

export const LAYOUT_LABELS: Record<Layout, string> = { table: "Table", board: "Board", calendar: "Calendar" };

/** The field a view's board is grouped by, if it is a choice field. */
export function boardField(description: Description, view: ViewSpec): string | null {
  const spec = view.group ? description.tables[view.table]?.fields[view.group] : undefined;
  return view.group && spec?.type === "choice" ? view.group : null;
}

/** The date field a calendar is laid out by: the first date field the view shows. */
export function calendarField(description: Description, view: ViewSpec): string | null {
  const table = description.tables[view.table];
  if (!table) return null;
  const shown = viewFields(description, view);
  return shown.find((n) => table.fields[n]?.type === "date") ?? Object.keys(table.fields).find((n) => table.fields[n]?.type === "date") ?? null;
}

export function layoutsFor(description: Description, view: ViewSpec): Layout[] {
  const out: Layout[] = ["table"];
  if (boardField(description, view)) out.push("board");
  if (calendarField(description, view)) out.push("calendar");
  return out;
}

/** A view that groups by a choice opens as a board; any other view opens as a table. */
export function defaultLayout(description: Description, view: ViewSpec): Layout {
  return boardField(description, view) ? "board" : "table";
}

/** A remembered layout if the view still offers it, else the default. */
export function chooseLayout(description: Description, view: ViewSpec, remembered: unknown): Layout {
  return layoutsFor(description, view).find((l) => l === remembered) ?? defaultLayout(description, view);
}

export type Column = {
  /** The group's value; null is the records with none. */
  value: Value;
  label: string;
  records: DescribedRecord[];
  /** How many match in all, when the server said; the page held may be fewer. */
  count: number;
  totals?: Record<string, Value>;
};

/** "No status" for a column of records that have none. */
export function emptyColumnLabel(field: string, spec: FieldSpec | undefined): string {
  return `No ${fieldLabel(field, spec).toLowerCase()}`;
}

/**
 * The records in groups by one field. A choice's groups follow its options, one for each even
 * when empty, with the records that have none last. Other fields group in the order the
 * server listed them, or in the order first met.
 */
export function groupRecords(table: TableSpec, field: string, records: DescribedRecord[], summaries: GroupSummary[] = []): Column[] {
  const spec = table.fields[field];
  const byValue = new Map<Value, DescribedRecord[]>();
  for (const record of records) {
    const value = record.values[field] ?? null;
    const key = spec?.type === "choice" && value !== null && !spec.options?.includes(String(value)) ? null : value;
    const list = byValue.get(key);
    if (list) list.push(record);
    else byValue.set(key, [record]);
  }
  const order: Value[] = [];
  if (spec?.type === "choice") order.push(...(spec.options ?? []));
  for (const s of summaries) if (!order.includes(s.value) && s.value !== null) order.push(s.value);
  for (const value of byValue.keys()) if (!order.includes(value) && value !== null) order.push(value);
  if (byValue.has(null) || summaries.some((s) => s.value === null && s.count > 0)) order.push(null);
  return order.map((value) => {
    const members = byValue.get(value) ?? [];
    const summary = summaries.find((s) => s.value === value);
    return {
      value,
      label: value === null ? emptyColumnLabel(field, spec) : String(value),
      records: members,
      count: summary ? summary.count : members.length,
      totals: summary?.totals,
    };
  });
}

/** The values to send to move a record to another column. */
export function moveValues(field: string, to: Value): Record<string, Value> {
  return { [field]: to };
}

/** The fields shown on a board card or a calendar chip: the view's own, without the title and the grouping field. */
export function cardFields(description: Description, view: ViewSpec, skip: readonly string[], most = 3): string[] {
  const table = description.tables[view.table];
  if (!table) return [];
  const title = Object.keys(table.fields).find((n) => table.fields[n]?.type === "text");
  return viewFields(description, view).filter((n) => n !== title && !skip.includes(n)).slice(0, most);
}

/** The field that names a record in a list: the first text field the view shows, else the first field. */
export function titleField(description: Description, view: ViewSpec): string | null {
  const table = description.tables[view.table];
  if (!table) return null;
  const shown = viewFields(description, view);
  return shown.find((n) => table.fields[n]?.type === "text") ?? null;
}
