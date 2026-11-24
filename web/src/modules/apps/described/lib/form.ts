// The form for one record, made from its table's fields. A form holds text for every
// control; `readForm` turns it into values to send, with a sentence for each field it
// cannot read. The server checks everything again and has the last word.

import type { DescribedRecord, FieldSpec, TableSpec, Value } from "../api.ts";
import { MAX_CELL, editableFields, fieldLabel } from "./schema.ts";
import { moneyInput, parseMoney } from "./values.ts";

export const MAX_TEXT = 2000;

/** One control's value: text, or a boolean for a checkbox. */
export type FormState = Record<string, string | boolean>;

export type Control = "text" | "textarea" | "int" | "number" | "money" | "checkbox" | "date" | "select" | "link";

/** Which control a field gets. The first text field is a line; the others take several lines. */
export function controlFor(table: TableSpec, name: string): Control {
  const spec = table.fields[name];
  if (!spec) return "text";
  switch (spec.type) {
    case "text":
      return Object.keys(table.fields).find((n) => table.fields[n]?.type === "text") === name ? "text" : "textarea";
    case "bool":
      return "checkbox";
    case "choice":
      return "select";
    case "computed":
      return "text";
    default:
      return spec.type;
  }
}

function startText(spec: FieldSpec, value: Value | undefined, locale?: string): string | boolean {
  if (spec.type === "bool") return value === true;
  if (value === null || value === undefined) return "";
  if (spec.type === "money" && typeof value === "number") return moneyInput(value, spec.currency ?? "USD", locale);
  return String(value);
}

/** The form for a new record, or for an existing one. `preset` fills fields of a new one. */
export function formFor(table: TableSpec, record?: DescribedRecord, preset: Record<string, Value> = {}, locale?: string): FormState {
  const state: FormState = {};
  for (const name of editableFields(table)) {
    const spec = table.fields[name];
    if (!spec) continue;
    state[name] = startText(spec, record ? record.values[name] : preset[name], locale);
  }
  return state;
}

export type FormRead = { values: Record<string, Value>; errors: Record<string, string> };

function range(spec: FieldSpec, whole: boolean): string {
  const noun = whole ? "a whole number" : "a number";
  if (spec.min !== undefined && spec.max !== undefined) return `${noun} from ${spec.min} to ${spec.max}`;
  if (spec.min !== undefined) return `${noun} of ${spec.min} or more`;
  if (spec.max !== undefined) return `${noun} of ${spec.max} or less`;
  return noun;
}

/** One field's text as a value, or a sentence saying what to write instead. */
export function readField(name: string, spec: FieldSpec, raw: string | boolean | undefined, locale?: string): { value: Value } | { error: string } {
  const label = fieldLabel(name, spec);
  if (spec.type === "bool") return { value: raw === true };
  const text = typeof raw === "string" ? raw : "";
  if (text.trim() === "") return spec.required ? { error: `${label} is needed. Fill it in.` } : { value: null };
  switch (spec.type) {
    case "int": {
      const n = /^-?\d{1,15}$/.test(text.trim()) ? Number(text.trim()) : NaN;
      if (!Number.isSafeInteger(n) || (spec.min !== undefined && n < spec.min) || (spec.max !== undefined && n > spec.max)) {
        return { error: `${label} must be ${range(spec, true)}.` };
      }
      return { value: n };
    }
    case "number": {
      const n = /^-?\d+(\.\d+)?$/.test(text.trim().replace(",", ".")) ? Number(text.trim().replace(",", ".")) : NaN;
      if (!Number.isFinite(n) || Math.abs(n) > Number.MAX_SAFE_INTEGER || (spec.min !== undefined && n < spec.min) || (spec.max !== undefined && n > spec.max)) {
        return { error: `${label} must be ${range(spec, false)}.` };
      }
      return { value: n };
    }
    case "money": {
      const minor = parseMoney(text, spec.currency ?? "USD", locale);
      if (minor === null || (spec.min !== undefined && minor < spec.min) || (spec.max !== undefined && minor > spec.max)) {
        return { error: `${label} must be an amount in ${spec.currency ?? "USD"}, such as 12.50${spec.min === 0 ? ", and not below zero" : ""}.` };
      }
      return { value: minor };
    }
    case "date":
      return /^\d{4}-\d{2}-\d{2}$/.test(text) && !Number.isNaN(Date.parse(`${text}T00:00:00Z`)) && new Date(`${text}T00:00:00Z`).toISOString().startsWith(text)
        ? { value: text }
        : { error: `${label} must be a real date.` };
    case "choice":
      return spec.options?.includes(text) ? { value: text } : { error: `${label} must be one of: ${(spec.options ?? []).join(", ")}.` };
    case "link": {
      const id = /^\d{1,15}$/.test(text) ? Number(text) : NaN;
      return Number.isSafeInteger(id) && id > 0 ? { value: id } : { error: `${label} must be picked from the list.` };
    }
    default:
      return text.length > MAX_TEXT ? { error: `${label} is ${text.length} characters long. Shorten it to ${MAX_TEXT} or fewer.` } : { value: text };
  }
}

export function readForm(table: TableSpec, state: FormState, locale?: string): FormRead {
  const values: Record<string, Value> = {};
  const errors: Record<string, string> = {};
  for (const name of editableFields(table)) {
    const spec = table.fields[name];
    if (!spec) continue;
    const read = readField(name, spec, state[name], locale);
    if ("error" in read) errors[name] = read.error;
    else values[name] = read.value;
  }
  return { values, errors };
}

/** For an edit: only the fields that differ from the record, so nothing else is touched. */
export function changesOnly(record: DescribedRecord, values: Record<string, Value>): Record<string, Value> {
  const out: Record<string, Value> = {};
  for (const [name, value] of Object.entries(values)) {
    const before = record.values[name] ?? null;
    if (before !== value) out[name] = value;
  }
  return out;
}

/** How wide a text control may grow before the form asks for shorter text. */
export const TEXT_ROWS = 3;
export const SHORT_TEXT = MAX_CELL;
