// A provider's record as a form: which of its type's fields are shown, the text in each
// box, and the options they become. The fields come from the server (GET /types), so a
// new type needs no screen of its own. Mirrors server/jimmyhq/hands/provider.py
// (RecordField) and options.py.

import type { Provider, ProviderType, RecordField } from "../api.ts";

/** Which of a type's two shapes a record is, when the type has both: a program on this machine or an address. */
export type Shape = "program" | "address";
export type FieldValues = Record<string, string>;

export const SHAPE_LABELS: Record<Shape, string> = { program: "A program on this computer", address: "An address" };

/** The shapes a type offers, in the order to show them. Empty when it has only one. */
export function shapesOf(type: ProviderType | undefined): Shape[] {
  const found = new Set((type?.fields ?? []).map((field) => field.only_for).filter((x): x is Shape => x === "program" || x === "address"));
  return (["program", "address"] as const).filter((shape) => found.has(shape));
}

/** The shape a saved record has, or the first on offer for a new one. */
export function startShape(type: ProviderType | undefined, provider: Pick<Provider, "base_url"> | null): Shape | null {
  const shapes = shapesOf(type);
  if (shapes.length === 0) return null;
  if (provider) return provider.base_url && shapes.includes("address") ? "address" : (shapes.includes("program") ? "program" : shapes[0]!);
  return shapes[0]!;
}

function applies(field: RecordField, shape: Shape | null): boolean {
  return !field.only_for || field.only_for === shape;
}

/** The option fields to show for this shape. The address is a field of the record itself and is not among them. */
export function optionFields(type: ProviderType | undefined, shape: Shape | null): RecordField[] {
  return (type?.fields ?? []).filter((field) => field.where === "options" && applies(field, shape));
}

/** The type's own word on the address box, or null when this shape has none. */
export function addressField(type: ProviderType | undefined, shape: Shape | null): RecordField | null {
  return (type?.fields ?? []).find((field) => field.where === "record" && field.name === "base_url" && applies(field, shape)) ?? null;
}

export function isList(field: Pick<RecordField, "kind">): boolean {
  return field.kind === "argv" || field.kind === "names";
}

/** One item on each line. Empty lines are dropped; nothing else about a line is changed. */
export function linesOf(text: string): string[] {
  return text.split("\n").map((line) => line.replace(/\r$/, "")).filter((line) => line.trim() !== "");
}

/** The text each box starts with, read from the saved options. */
export function valuesFromOptions(fields: readonly RecordField[], options: Record<string, unknown> | undefined): FieldValues {
  const out: FieldValues = {};
  for (const field of fields) {
    const value = options?.[field.name];
    if (isList(field)) out[field.name] = Array.isArray(value) ? value.filter((item) => typeof item === "string").join("\n") : "";
    else out[field.name] = typeof value === "string" || typeof value === "number" ? String(value) : "";
  }
  return out;
}

export type OptionsResult = { ok: true; options: Record<string, unknown> } | { ok: false; errors: Record<string, string> };

// What the server also refuses in a program's arguments: said here so the owner hears it before saving.
const NAMED_SECRET = /^-{0,2}[a-z0-9_.-]*?(pass(word|wd|phrase|code)?|secret|token|api[_-]?key|apikey|key|auth|authorization|credentials?|bearer)s?[=:](.+)$/i;
const REFERENCE = /\{secret:[a-z][a-z0-9_-]{0,39}\}/;
export const SECRET_ADVICE = "Save it as a secret of this provider instead, and write {secret:NAME} in its place.";

/** Why one argument cannot be saved as written, or null. The server makes the fuller check. */
export function argumentProblem(item: string): string | null {
  const named = NAMED_SECRET.exec(item);
  if (named && !REFERENCE.test(named[3] ?? "")) return `"${item.split(/[=:]/)[0]}" names a key, a token or a password, and its value is written out. ${SECRET_ADVICE}`;
  if (item.includes("{secret:") && !REFERENCE.test(item)) return "A secret is named as {secret:NAME}, where NAME starts with a letter and uses lower-case letters, digits, - and _.";
  return null;
}

/**
 * The options to save: what the boxes hold for the fields in view, on top of any saved
 * option this screen has no box for. A field of the other shape is removed, so a record
 * is never both a program and an address.
 */
export function optionsFromValues(type: ProviderType | undefined, shape: Shape | null, values: FieldValues, saved: Record<string, unknown> | undefined): OptionsResult {
  const declared = new Set((type?.fields ?? []).filter((field) => field.where === "options").map((field) => field.name));
  const options: Record<string, unknown> = Object.fromEntries(Object.entries(saved ?? {}).filter(([name]) => !declared.has(name)));
  const errors: Record<string, string> = {};
  for (const field of optionFields(type, shape)) {
    const text = values[field.name] ?? "";
    if (text.trim() === "") {
      if (field.required) errors[field.name] = `${field.label} is needed.`;
      continue;
    }
    if (isList(field)) {
      const items = linesOf(text);
      if (field.kind === "names") {
        const names = items.map((item) => item.trim());
        if (names.some((name) => !/^[A-Za-z_][A-Za-z0-9_-]*$/.test(name))) errors[field.name] = "Names only, one on each line: letters, digits, - and _. A value is saved as a secret, never written here.";
        else if (new Set(names.map((name) => name.toLowerCase())).size !== names.length) errors[field.name] = "The same name is listed twice.";
        else options[field.name] = names;
      } else {
        const problem = items.map(argumentProblem).find((said) => said !== null);
        if (problem) errors[field.name] = problem;
        else options[field.name] = items;
      }
    } else if (field.kind === "number") {
      const n = Number(text.trim());
      if (!Number.isFinite(n) || n <= 0) errors[field.name] = "This must be a number above 0, or left empty.";
      else options[field.name] = n;
    } else {
      options[field.name] = text.trim();
    }
  }
  return Object.keys(errors).length > 0 ? { ok: false, errors } : { ok: true, options };
}

/** True when two sets of options would save the same record. */
export function sameOptions(a: Record<string, unknown> | undefined, b: Record<string, unknown> | undefined): boolean {
  const flat = (options: Record<string, unknown> | undefined) => JSON.stringify(Object.entries(options ?? {}).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0)));
  return flat(a) === flat(b);
}

/** Secrets the record refers to by name that have no saved value yet. */
export function missingSecrets(provider: Pick<Provider, "secrets" | "secrets_wanted">): string[] {
  const saved = new Set(provider.secrets);
  return (provider.secrets_wanted ?? []).filter((name) => !saved.has(name));
}

/** The secret a name in a "names" field is saved under, as the help line says it. */
export function secretFor(field: Pick<RecordField, "secret_prefix">, name: string): string {
  return `${field.secret_prefix ?? ""}${name.trim().toLowerCase()}`;
}
