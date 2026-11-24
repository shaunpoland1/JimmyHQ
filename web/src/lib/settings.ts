// Settings value coercion and comparison. The settings UI is generated from
// the server's schema, so everything here works from a setting's `type`.

import type { Setting, SettingValue } from "../api.ts";

export type Coerced = { ok: true; value: SettingValue } | { ok: false; error: string };

type Shape = Pick<Setting, "type" | "min" | "max" | "choices">;

function fail(error: string): Coerced {
  return { ok: false, error };
}

function coerceNumber(shape: Shape, raw: unknown): Coerced {
  const text = typeof raw === "string" ? raw.trim() : raw;
  if (text === "" || text === null || text === undefined || typeof text === "boolean") return fail("Enter a number.");
  const value = typeof text === "number" ? text : Number(text);
  if (!Number.isFinite(value)) return fail("Enter a number.");
  if (shape.type === "int" && !Number.isInteger(value)) return fail("Enter a whole number.");
  if (shape.min !== undefined && value < shape.min) return fail(`Must be at least ${shape.min}.`);
  if (shape.max !== undefined && value > shape.max) return fail(`Must be at most ${shape.max}.`);
  return { ok: true, value };
}

/** Turns raw UI input into a value of the setting's type, or explains why not. */
export function coerceSettingValue(shape: Shape, raw: unknown): Coerced {
  switch (shape.type) {
    case "bool":
      if (typeof raw === "boolean") return { ok: true, value: raw };
      if (raw === "true") return { ok: true, value: true };
      if (raw === "false") return { ok: true, value: false };
      return fail("Choose on or off.");
    case "choice": {
      const match = (shape.choices ?? []).find((choice) => String(choice.value) === String(raw));
      return match ? { ok: true, value: match.value } : fail("Choose one of the listed options.");
    }
    case "int":
    case "float":
      return coerceNumber(shape, raw);
    case "str":
      return typeof raw === "string" ? { ok: true, value: raw } : fail("Enter text.");
    case "list": {
      if (!Array.isArray(raw)) return fail("Expected a list.");
      const items = raw.map((item) => String(item).trim()).filter((item) => item.length > 0);
      return { ok: true, value: items };
    }
    default:
      return fail("Unsupported setting type.");
  }
}

export function valuesEqual(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((item, index) => item === b[index]);
  }
  return a === b;
}

export function isDefaultValue(setting: Pick<Setting, "value" | "default">): boolean {
  return valuesEqual(setting.value, setting.default);
}

/** Text shown in the "Default: ..." hint. */
export function describeValue(setting: Pick<Setting, "type" | "choices">, value: unknown): string {
  if (setting.type === "bool") return value ? "On" : "Off";
  if (setting.type === "choice") {
    const match = (setting.choices ?? []).find((choice) => String(choice.value) === String(value));
    return match ? match.label : String(value);
  }
  if (Array.isArray(value)) return value.length ? value.join(", ") : "empty";
  if (value === "" || value === null || value === undefined) return "empty";
  return String(value);
}

export function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
}

/** Returns a copy with the item at `from` moved to `to`; out-of-range is a no-op. */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const copy = [...items];
  if (from < 0 || from >= copy.length || to < 0 || to >= copy.length || from === to) return copy;
  const [item] = copy.splice(from, 1);
  copy.splice(to, 0, item as T);
  return copy;
}

export type DropSide = "before" | "after";

/**
 * Where the item at `from` lands when it is dropped on the item at `over`,
 * above it or below it. Null when that would leave the list as it is.
 */
export function dropIndex(length: number, from: number, over: number, side: DropSide): number | null {
  if (from < 0 || from >= length || over < 0 || over >= length || from === over) return null;
  // Taking the item out first shifts everything after it up by one.
  const slot = side === "before" ? over : over + 1;
  const to = from < slot ? slot - 1 : slot;
  return to === from ? null : to;
}

/** What a screen reader is told after an item moves. */
export function moveAnnouncement(label: string, index: number, total: number): string {
  return `${label} moved to position ${index + 1} of ${total}.`;
}

export function removeItem<T>(items: readonly T[], index: number): T[] {
  return items.filter((_, i) => i !== index);
}

/** Appends a trimmed item, ignoring blanks and exact duplicates. */
export function addItem(items: readonly string[], raw: string): string[] {
  const item = raw.trim();
  if (!item || items.includes(item)) return [...items];
  return [...items, item];
}
