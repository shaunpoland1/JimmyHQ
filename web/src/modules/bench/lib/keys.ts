// The Bench's single-key shortcuts.

import type { Action } from "../api.ts";

export type BenchCommand = "next" | "previous" | "open" | "primary" | "notYet" | "danger" | "snooze" | "help";

export type KeyPress = {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  /** True when the key is being held down and the press is a repeat. */
  repeat?: boolean;
  /** Tag name of the element the key was pressed in, e.g. "INPUT". */
  targetTag?: string;
  /** True when that element is editable text (contenteditable). */
  editable?: boolean;
  /** ARIA role of that element, if any. */
  targetRole?: string | null;
  /** True when focus is on something outside the Bench: the sidebar, the top strip or the dock. */
  outside?: boolean;
  /** True when focus is on nothing at all: the page itself, as after a click on plain text. */
  nowhere?: boolean;
  /** True when the owner has switched the single-letter keys off (`bench.single_keys`). */
  lettersOff?: boolean;
};

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
// Enter already means "press" on these, so it must not also open the card.
const PRESSABLE_TAGS = new Set(["BUTTON", "A", "SUMMARY"]);
const PRESSABLE_ROLES = new Set(["button", "link", "tab", "menuitem", "switch", "option"]);

export function isTyping(press: Pick<KeyPress, "targetTag" | "editable">): boolean {
  return Boolean(press.editable) || TYPING_TAGS.has((press.targetTag ?? "").toUpperCase());
}

// These act on the open card, so they need the focus to be in the Bench itself.
const ACTS_ON_CARD: ReadonlySet<BenchCommand> = new Set(["primary", "notYet", "danger", "snooze"]);

/**
 * The command a key press means on the Bench, or null. Never acts while typing
 * in a field, or while focus is outside the Bench: a key pressed on a control
 * in the dock or the sidebar is not the Bench's, and must not decide a card.
 * With focus on nothing (the page itself) the keys still move, open and show the
 * help, but none acts on a card: a stray letter after a click on plain text must
 * not approve what happens to be open. With the single-letter keys switched off
 * only Enter is left.
 */
export function benchCommand(press: KeyPress): BenchCommand | null {
  const command = commandFor(press);
  if (command === null) return null;
  if (press.lettersOff && command !== "open") return null;
  if (press.nowhere && ACTS_ON_CARD.has(command)) return null;
  return command;
}

function commandFor(press: KeyPress): BenchCommand | null {
  if (press.outside) return null;
  if (press.metaKey || press.ctrlKey || press.altKey) return null;
  if (isTyping(press)) return null;
  if (press.key === "Enter") {
    if (press.repeat) return null;
    const tag = (press.targetTag ?? "").toUpperCase();
    if (PRESSABLE_TAGS.has(tag) || PRESSABLE_ROLES.has(press.targetRole ?? "")) return null;
    return "open";
  }
  const key = press.key.toLowerCase();
  if (key === "j") return "next";
  if (key === "k") return "previous";
  // Only moving repeats: a held key must never decide one card after another.
  if (press.repeat) return null;
  if (press.key === "?") return "help";
  switch (key) {
    case "a":
      return "primary";
    case "n":
      return "notYet";
    case "d":
      return "danger";
    case "s":
      return "snooze";
    default:
      return null;
  }
}

/** The card's own action a shortcut stands for, if it has one. */
export function actionFor(actions: readonly Action[], command: BenchCommand): Action | undefined {
  switch (command) {
    case "primary":
      return actions.find((action) => action.style === "primary");
    case "notYet":
      return (
        actions.find((action) => action.id === "not_yet") ??
        actions.find((action) => !action.closes && action.moves_to === "waiting" && action.style !== "danger")
      );
    case "danger":
      return actions.find((action) => action.style === "danger");
    default:
      return undefined;
  }
}

export const KEY_HELP: readonly { keys: string[]; does: string }[] = [
  { keys: ["j", "k"], does: "Move down and up the list" },
  { keys: ["Enter"], does: "Open the card and focus the reading pane" },
  { keys: ["a"], does: "The card's main action, such as Approve" },
  { keys: ["n"], does: "Not yet, if the card offers it" },
  { keys: ["d"], does: "The card's No action; asks for the reason first" },
  { keys: ["s"], does: "Open the snooze menu" },
  { keys: ["?"], does: "Show these keys" },
];

/** The sentence under the list of keys. It has to say what `benchCommand` does. */
export const KEY_RULES =
  "Keys do nothing while you are typing in a field, or while focus is in the sidebar, the top strip or the dock. " +
  "a, n, d and s act on the open card only while focus is in the Bench: press Enter or click the card first.";
export const KEYS_OFF_HINT = "You can switch the letter keys off in Settings, under Bench.";
export const KEYS_OFF_NOTE = "The letter keys are switched off. Enter still opens a card. You can switch them back on in Settings, under Bench.";

/** Whether the owner has the single-letter keys on. Anything but a plain false leaves them on. */
export function singleKeysOn(value: unknown): boolean {
  return value !== false;
}
