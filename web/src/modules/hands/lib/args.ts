// An action's arguments: which shape each one has, what the owner may type for a fixed
// value, and how that becomes the value the server stores. Mirrors server/jimmyhq/hands/rules.py.

import type { ArgChoice, ArgMode, Schema } from "../api.ts";

/** The flat type a tool argument can have, or null for anything nested. */
export type FlatType = "string" | "number" | "integer" | "boolean" | "enum" | "strings";

export function flatType(schema: Schema): FlatType | null {
  let kind: unknown = schema.type;
  if (Array.isArray(kind)) {
    const kinds = kind.filter((k) => k !== "null");
    kind = kinds.length === 1 ? kinds[0] : null;
  }
  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    const ok = schema.enum.every((option) => typeof option === "string") && (kind === undefined || kind === null || kind === "string");
    return ok ? "enum" : null;
  }
  if (kind === "string" || kind === "number" || kind === "integer" || kind === "boolean") return kind;
  if (kind === "array") {
    const items = schema.items;
    if (items && typeof items === "object" && items.type === "string" && !("enum" in items)) return "strings";
  }
  return null;
}

export type ArgRow = {
  name: string;
  required: boolean;
  description: string;
  schema: Schema;
  kind: FlatType | null;
  options: string[];
};

/** An action's arguments in a steady order: required ones first, then by name. */
export function argumentRows(schema: Schema | undefined): ArgRow[] {
  const props = schema && typeof schema.properties === "object" && schema.properties ? schema.properties : {};
  const required = new Set(Array.isArray(schema?.required) ? schema.required.map(String) : []);
  const rows = Object.entries(props).map(([name, raw]) => {
    const part: Schema = raw && typeof raw === "object" ? raw : {};
    const kind = flatType(part);
    return {
      name,
      required: required.has(name),
      description: typeof part.description === "string" ? part.description : "",
      schema: part,
      kind,
      options: kind === "enum" ? (part.enum as string[]) : [],
    };
  });
  return rows.sort((a, b) => Number(b.required) - Number(a.required) || a.name.localeCompare(b.name));
}

export const MODE_LABELS: Record<ArgMode, string> = {
  open: "The agent fills this in",
  fixed: "Always this value",
  hidden: "Not used",
};

export function modeSentence(mode: ArgMode): string {
  if (mode === "open") return "The agent decides this each time it asks.";
  if (mode === "fixed") return "You choose the value. The agent never sees it and cannot change it.";
  return "Left out of every request.";
}

/** A short name for the kind of value an argument takes. */
export function kindLabel(kind: FlatType | null): string {
  switch (kind) {
    case "string":
      return "text";
    case "number":
      return "a number";
    case "integer":
      return "a whole number";
    case "boolean":
      return "yes or no";
    case "enum":
      return "one of a list";
    case "strings":
      return "a list of text";
    default:
      return "a structured value";
  }
}

/** Why an argument cannot be left to the agent, or null when it can. */
export function notPlain(row: ArgRow): string | null {
  if (row.kind !== null) return null;
  return `"${row.name}" is not a plain value (text, a number, yes or no, one of a list, or a list of text), so an agent cannot fill it in yet. Give it a fixed value or hide it.`;
}

// --- the value the owner types for a fixed argument ----------------------------------

export type Parsed = { ok: true; value: unknown } | { ok: false; error: string };

/** What the owner's text means for an argument of this kind. Blank is refused: a fixed argument needs its value. */
export function parseFixed(row: Pick<ArgRow, "name" | "kind" | "options">, text: string): Parsed {
  const name = row.name;
  switch (row.kind) {
    case "string":
      return text.trim() === "" ? { ok: false, error: `"${name}" is fixed, so it needs the value to use.` } : { ok: true, value: text };
    case "enum":
      return row.options.includes(text) ? { ok: true, value: text } : { ok: false, error: `Pick one of the choices for "${name}".` };
    case "boolean":
      return text === "true" ? { ok: true, value: true } : text === "false" ? { ok: true, value: false } : { ok: false, error: `Choose yes or no for "${name}".` };
    case "number":
    case "integer": {
      const trimmed = text.trim();
      const value = Number(trimmed);
      if (trimmed === "" || !Number.isFinite(value)) return { ok: false, error: `"${name}" must be a number.` };
      if (row.kind === "integer" && !Number.isInteger(value)) return { ok: false, error: `"${name}" must be a whole number.` };
      return { ok: true, value };
    }
    case "strings": {
      const items = text.split("\n").map((line) => line.trim()).filter(Boolean);
      return items.length === 0 ? { ok: false, error: `"${name}" is fixed, so it needs at least one item. Put one on each line.` } : { ok: true, value: items };
    }
    default: {
      if (text.trim() === "") return { ok: false, error: `"${name}" is fixed, so it needs the value to use.` };
      try {
        return { ok: true, value: JSON.parse(text) as unknown };
      } catch {
        return { ok: false, error: `"${name}" needs a value written as JSON, for example {"a": 1}.` };
      }
    }
  }
}

/** A stored fixed value as the text box shows it. */
export function fixedText(row: Pick<ArgRow, "kind">, value: unknown): string {
  if (value === null || value === undefined) return "";
  switch (row.kind) {
    case "string":
    case "enum":
      return typeof value === "string" ? value : String(value);
    case "boolean":
      return value === true ? "true" : value === false ? "false" : "";
    case "number":
    case "integer":
      return String(value);
    case "strings":
      return Array.isArray(value) ? value.map(String).join("\n") : String(value);
    default:
      return JSON.stringify(value, null, 2);
  }
}

// --- the mode of every argument, as one form ----------------------------------------

export type ArgDraft = { mode: ArgMode; text: string };
export type ArgsDraft = Record<string, ArgDraft>;

/** The form's starting state from the server's choices (its defaults, or a saved rule). */
export function argsDraft(rows: readonly ArgRow[], choices: Record<string, ArgChoice> | undefined): ArgsDraft {
  const out: ArgsDraft = {};
  for (const row of rows) {
    const choice = choices?.[row.name];
    const mode: ArgMode = choice?.mode ?? (row.required ? "open" : "hidden");
    out[row.name] = { mode, text: mode === "fixed" ? fixedText(row, choice?.value) : "" };
  }
  return out;
}

export type ArgsResult = { ok: true; body: Record<string, ArgChoice> } | { ok: false; errors: Record<string, string> };

/** The request body for the arguments, or one sentence for each that is wrong. */
export function argsBody(rows: readonly ArgRow[], draft: ArgsDraft): ArgsResult {
  const body: Record<string, ArgChoice> = {};
  const errors: Record<string, string> = {};
  for (const row of rows) {
    const entry = draft[row.name] ?? { mode: row.required ? "open" : "hidden", text: "" };
    if (entry.mode === "fixed") {
      const parsed = parseFixed(row, entry.text);
      if (parsed.ok) body[row.name] = { mode: "fixed", value: parsed.value };
      else errors[row.name] = parsed.error;
    } else if (entry.mode === "hidden") {
      if (row.required) errors[row.name] = `"${row.name}" is required by this action, so it cannot be left out. Let the agent fill it in, or give it a fixed value.`;
      else body[row.name] = { mode: "hidden", value: null };
    } else {
      const problem = notPlain(row);
      if (problem) errors[row.name] = problem;
      else body[row.name] = { mode: "open", value: null };
    }
  }
  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, body };
}

/** The modes an argument may be set to: not "open" when the agent could not fill it in. */
export function modesFor(row: ArgRow): ArgMode[] {
  const all: ArgMode[] = row.required ? ["open", "fixed"] : ["open", "fixed", "hidden"];
  return all;
}

// --- trying a reading ---------------------------------------------------------------

/** The arguments for the owner's "try it": what was typed, checked against each argument's shape. Blank optional ones are left out. */
export function tryArguments(rows: readonly ArgRow[], typed: Record<string, string>): { ok: true; args: Record<string, unknown> } | { ok: false; errors: Record<string, string> } {
  const args: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const row of rows) {
    const text = typed[row.name] ?? "";
    if (text.trim() === "") {
      if (row.required) errors[row.name] = `"${row.name}" is needed to try this.`;
      continue;
    }
    const parsed = parseFixed(row, text);
    if (parsed.ok) args[row.name] = parsed.value;
    else errors[row.name] = parsed.error;
  }
  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, args };
}

/** Whether an action may be tried from the screen: it is known to only read. Mirrors the server. */
export function canTry(action: { effect: string; effect_from: string }): boolean {
  return action.effect === "read" && (action.effect_from === "provider" || action.effect_from === "adapter");
}
