// Advanced settings: fine tuning most people never need. Each headed block of
// a settings page keeps them behind one quiet control. A setting changed from
// its default is always shown, so nothing the owner altered is out of sight.

import type { Setting } from "../api.ts";
import { isDefaultValue } from "./settings.ts";

type Row = Pick<Setting, "key" | "value" | "default" | "advanced">;

export type AdvancedSplit<S> = {
  /** The rows to draw, in their usual order. */
  shown: S[];
  /** How many advanced rows the block has. */
  advanced: number;
  /** How many of them are out of sight right now. */
  hidden: number;
};

/**
 * Which rows of a block to draw. With `open` false an advanced row is drawn
 * only when it was changed from its default, or when it is the one a link
 * brought the reader to (`revealKey`).
 */
export function splitAdvanced<S extends Row>(rows: readonly S[], open: boolean, revealKey: string | null = null): AdvancedSplit<S> {
  const advanced = rows.filter((row) => row.advanced === true).length;
  const shown = rows.filter((row) => row.advanced !== true || open || row.key === revealKey || !isDefaultValue(row));
  return { shown, advanced, hidden: rows.length - shown.length };
}

/** The words on the control under a block; null when the block has nothing advanced. */
export function advancedToggleLabel(split: Pick<AdvancedSplit<unknown>, "advanced" | "hidden">, open: boolean): string | null {
  if (split.advanced === 0) return null;
  if (open) return `Hide advanced settings (${split.advanced})`;
  // All of them are in sight already because each one was changed.
  if (split.hidden === 0) return null;
  return `Show advanced settings (${split.hidden})`;
}

/** Where the browser remembers which blocks have their advanced settings open. */
export const ADVANCED_STORAGE_KEY = "jimmyhq.settings.advanced-open";

type Store = Pick<Storage, "getItem" | "setItem">;

/** The section ids whose advanced settings were left open in this browser. Never throws. */
export function readAdvancedOpen(store: Store | null): string[] {
  try {
    const raw = store?.getItem(ADVANCED_STORAGE_KEY);
    if (!raw) return [];
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === "string"))] : [];
  } catch {
    return [];
  }
}

/** Remember them. Storage that is full, blocked or missing is not an error: the choice then lasts for this visit. */
export function writeAdvancedOpen(store: Store | null, open: readonly string[]): void {
  try {
    store?.setItem(ADVANCED_STORAGE_KEY, JSON.stringify([...new Set(open)].sort()));
  } catch {
    // Nothing to do.
  }
}

/** The list with one section opened or closed. */
export function toggleAdvancedOpen(open: readonly string[], section: string, next: boolean): string[] {
  const rest = open.filter((item) => item !== section);
  return next ? [...rest, section] : rest;
}
