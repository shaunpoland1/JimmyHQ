// The list's keyboard map.

export type ListAction = "next" | "prev" | "tick" | "edit" | "new" | "push" | "open" | "moveUp" | "moveDown" | "undo";

export type KeyPress = { key: string; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean; repeat?: boolean };

export type KeyContext = {
  /** Focus is in a text field, select or editable region. */
  typing: boolean;
  /** Focus is on a button, link or other control that uses Space and Enter itself. */
  onControl: boolean;
  /** A dialog is open. */
  dialogOpen: boolean;
};

/** What a key press means on the task list, or null when the list should leave it alone. */
export function listAction(press: KeyPress, context: KeyContext): ListAction | null {
  const action = actionFor(press, context);
  // A held key may keep moving the selection, but must not tick or push row after row.
  if (press.repeat && action !== "next" && action !== "prev") return null;
  return action;
}

function actionFor(press: KeyPress, context: KeyContext): ListAction | null {
  if (context.typing || context.dialogOpen) return null;
  if (press.ctrlKey || press.metaKey) return null;
  if (press.altKey) {
    if (press.key === "ArrowUp") return "moveUp";
    if (press.key === "ArrowDown") return "moveDown";
    return null;
  }
  switch (press.key) {
    case "j":
    case "ArrowDown":
      return "next";
    case "k":
    case "ArrowUp":
      return "prev";
    case "x":
      return "tick";
    case " ":
      return context.onControl ? null : "tick";
    case "Enter":
      return context.onControl ? null : "open";
    case "e":
      return "edit";
    case "n":
      return "new";
    case "p":
      return "push";
    case "u":
      return "undo";
    default:
      return null;
  }
}

/** The id selected after moving by `delta`; clamps at the ends and starts at the nearest end. */
export function stepSelection(ids: readonly string[], current: string | null, delta: -1 | 1): string | null {
  if (ids.length === 0) return null;
  const at = current === null ? -1 : ids.indexOf(current);
  if (at < 0) return (delta > 0 ? ids[0] : ids[ids.length - 1]) ?? null;
  return ids[Math.min(ids.length - 1, Math.max(0, at + delta))] ?? null;
}

/** After a row leaves the list, the row that should take the selection. */
export function selectionAfterRemoval(ids: readonly string[], removed: string): string | null {
  const at = ids.indexOf(removed);
  if (at < 0) return null;
  return ids[at + 1] ?? ids[at - 1] ?? null;
}

export const KEY_HINTS: readonly { keys: string; label: string }[] = [
  { keys: "j / k", label: "move" },
  { keys: "x", label: "tick" },
  { keys: "e / enter", label: "edit" },
  { keys: "p", label: "push a day" },
  { keys: "n", label: "new" },
  { keys: "u", label: "undo" },
  { keys: "alt ↑ / ↓", label: "reorder" },
];
