// The Memory screen's keys.

export type MemoryCommand = "search" | "newNote" | "edit" | "save" | "leaveEdit";

export type KeyPress = {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  /** True when the key is being held down and the press is a repeat. */
  repeat?: boolean;
  /** Tag name of the element the key was pressed in, e.g. "INPUT". */
  targetTag?: string;
  /** True when that element is editable text (contenteditable). */
  editable?: boolean;
  /** True when focus is on something outside the Memory screen: the sidebar, the top strip or the dock. */
  outside?: boolean;
  /** True while a dialog is open anywhere: it owns the keyboard. */
  dialogOpen?: boolean;
  /** True when the key was pressed in the note editor's own text area. */
  inEditor?: boolean;
  /** True while the open note is in Edit mode. */
  editing?: boolean;
  /** True when a note is open. */
  hasNote?: boolean;
};

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export function isTyping(press: Pick<KeyPress, "targetTag" | "editable">): boolean {
  return Boolean(press.editable) || TYPING_TAGS.has((press.targetTag ?? "").toUpperCase());
}

/**
 * The command a key press means on the Memory screen, or null. Single letters
 * never act while typing in a field, under a dialog, or while focus is outside
 * the screen. Saving and leaving Edit mode are the editor's own keys, so they
 * work from inside its text area, and only there or outside any field.
 */
export function memoryCommand(press: KeyPress): MemoryCommand | null {
  if (press.outside || press.dialogOpen) return null;
  const typing = isTyping(press);
  const key = press.key.toLowerCase();

  if ((press.metaKey || press.ctrlKey) && !press.altKey && key === "s") {
    if (!press.editing || press.repeat) return null;
    // In another field of the screen (the search box, say) the browser keeps its own meaning.
    return typing && !press.inEditor ? null : "save";
  }
  if (press.metaKey || press.ctrlKey || press.altKey) return null;
  if (press.key === "Escape") {
    if (!press.editing || press.repeat) return null;
    return typing && !press.inEditor ? null : "leaveEdit";
  }
  if (typing || press.repeat) return null;
  if (press.key === "/") return "search";
  if (key === "n") return "newNote";
  if (key === "e") return press.hasNote && !press.editing ? "edit" : null;
  return null;
}

export const KEY_HELP: readonly { keys: string[]; does: string }[] = [
  { keys: ["/"], does: "Search memory" },
  { keys: ["n"], does: "Start a new note" },
  { keys: ["e"], does: "Edit the open note" },
  { keys: ["Ctrl S", "⌘ S"], does: "Save while editing" },
  { keys: ["Esc"], does: "Leave Edit mode" },
];
