// Reading a described app's description. It comes from an app's manifest, so it is data from
// outside: this file keeps only the shapes the renderer knows, caps every length, and turns
// every label into plain one-line text. Nothing it returns is ever used as markup.

import type { Description, FieldSpec, FieldType, FilterRule, TableSpec, TotalKind, ViewSpec } from "../api.ts";

export const MAX_LABEL = 60;
export const MAX_CELL = 200;
export const MAX_TABLES = 30;
export const MAX_VIEWS = 40;
export const MAX_FIELDS = 60;
export const MAX_OPTIONS = 50;

const FIELD_TYPES: readonly FieldType[] = ["text", "int", "number", "bool", "date", "choice", "money", "link", "computed"];
const TOTALS: readonly TotalKind[] = ["sum", "avg", "min", "max", "count"];
const NAME = /^[a-z][a-z0-9_]{0,30}$/;

/** One line of plain text: control and invisible characters removed, spaces collapsed, cut to `most` characters. */
export function cleanText(value: unknown, most: number = MAX_CELL): string {
  if (typeof value !== "string") return "";
  const text = value.replace(/[\p{C}\p{Zl}\p{Zp}]/gu, " ").replace(/\s+/g, " ").trim();
  const letters = Array.from(text);
  return letters.length <= most ? text : `${letters.slice(0, most - 1).join("").trimEnd()}…`;
}

/** `finished_on` becomes "Finished on": the label a name gets when the app gave none. */
export function humanise(name: string): string {
  const words = name.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
}

export function fieldLabel(name: string, spec: Pick<FieldSpec, "label"> | undefined): string {
  return cleanText(spec?.label, MAX_LABEL) || humanise(name);
}

/** What one record of a table is called, in a sentence: "book". */
export function recordNoun(table: string): string {
  return humanise(table).toLowerCase();
}

export function tableLabel(table: string, spec: Pick<TableSpec, "label"> | undefined): string {
  return cleanText(spec?.label, MAX_LABEL) || humanise(table);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function readField(raw: unknown): FieldSpec | null {
  if (!isObject(raw) || !FIELD_TYPES.includes(raw.type as FieldType)) return null;
  const spec: FieldSpec = { type: raw.type as FieldType };
  if (typeof raw.label === "string") spec.label = cleanText(raw.label, MAX_LABEL);
  if (raw.required === true) spec.required = true;
  const min = finite(raw.min);
  const max = finite(raw.max);
  if (min !== undefined) spec.min = min;
  if (max !== undefined) spec.max = max;
  if (spec.type === "choice") {
    const options = Array.isArray(raw.options) ? raw.options.filter((o): o is string => typeof o === "string").slice(0, MAX_OPTIONS) : [];
    spec.options = options.map((o) => cleanText(o, MAX_LABEL)).filter((o, i, all) => o && all.indexOf(o) === i);
  }
  if (spec.type === "money") spec.currency = typeof raw.currency === "string" && /^[A-Z]{3}$/.test(raw.currency) ? raw.currency : "USD";
  // A link is only ever the name of another table in this same app, never an address.
  if (spec.type === "link") spec.table = typeof raw.table === "string" && NAME.test(raw.table) ? raw.table : "";
  return spec;
}

function readFilter(raw: unknown): FilterRule[] {
  if (!Array.isArray(raw)) return [];
  const out: FilterRule[] = [];
  for (const rule of raw.slice(0, 10)) {
    if (!isObject(rule) || typeof rule.field !== "string" || typeof rule.op !== "string") continue;
    const one: FilterRule = { field: rule.field, op: rule.op };
    if (["string", "number", "boolean"].includes(typeof rule.value)) one.value = rule.value as string | number | boolean;
    out.push(one);
  }
  return out;
}

/**
 * The description as the renderer will use it. Unknown field types, tables without fields,
 * views of a table that is not there and names that are not field names are dropped, not
 * repaired; the server has already refused such a manifest, so this only matters for a
 * manifest that did not come through it.
 */
export function readDescription(raw: unknown): Description {
  const tables: Record<string, TableSpec> = {};
  const source = isObject(raw) && isObject(raw.tables) ? raw.tables : {};
  for (const [name, table] of Object.entries(source).slice(0, MAX_TABLES)) {
    if (!NAME.test(name) || !isObject(table) || !isObject(table.fields)) continue;
    const fields: Record<string, FieldSpec> = {};
    for (const [field, spec] of Object.entries(table.fields).slice(0, MAX_FIELDS)) {
      const read = NAME.test(field) && field !== "id" ? readField(spec) : null;
      if (read) fields[field] = read;
    }
    if (Object.keys(fields).length === 0) continue;
    tables[name] = { fields, ...(typeof table.label === "string" ? { label: cleanText(table.label, MAX_LABEL) } : {}) };
  }
  for (const table of Object.values(tables)) {
    for (const [name, field] of Object.entries(table.fields)) {
      if (field.type === "link" && !(field.table && field.table in tables)) delete table.fields[name];
    }
  }
  const views: ViewSpec[] = [];
  const list = isObject(raw) && Array.isArray(raw.views) ? raw.views : [];
  for (const raw of list.slice(0, MAX_VIEWS)) {
    if (!isObject(raw) || typeof raw.id !== "string" || typeof raw.table !== "string") continue;
    const table = tables[raw.table];
    if (!NAME.test(raw.id) || !table || views.some((v) => v.id === raw.id)) continue;
    const known = (n: unknown): n is string => typeof n === "string" && n in table.fields;
    const view: ViewSpec = { id: raw.id, label: cleanText(raw.label, MAX_LABEL) || humanise(raw.id), table: raw.table };
    if (Array.isArray(raw.fields)) {
      const shown = raw.fields.filter(known).filter((n, i, all) => all.indexOf(n) === i);
      if (shown.length) view.fields = shown;
    }
    const filter = readFilter(raw.filter).filter((rule) => known(rule.field));
    if (filter.length) view.filter = filter;
    if (Array.isArray(raw.sort)) {
      const sort = raw.sort.filter((k): k is string => typeof k === "string" && known(k.startsWith("-") ? k.slice(1) : k)).slice(0, 5);
      if (sort.length) view.sort = sort;
    }
    if (known(raw.group)) view.group = raw.group;
    if (isObject(raw.totals)) {
      const totals: Record<string, TotalKind> = {};
      for (const [field, how] of Object.entries(raw.totals)) {
        if (known(field) && TOTALS.includes(how as TotalKind)) totals[field] = how as TotalKind;
      }
      if (Object.keys(totals).length) view.totals = totals;
    }
    views.push(view);
  }
  return { tables, views };
}

/** The fields a view shows, in order: the ones it names, or every field of the table. */
export function viewFields(description: Description, view: ViewSpec): string[] {
  const fields = description.tables[view.table]?.fields ?? {};
  const names = view.fields ?? Object.keys(fields);
  return names.filter((n) => n in fields);
}

/** The fields a person can type into: everything except a computed field. */
export function editableFields(table: TableSpec): string[] {
  return Object.keys(table.fields).filter((n) => table.fields[n]?.type !== "computed");
}
