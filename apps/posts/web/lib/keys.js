// The keys on the Drafts view, shaped for a five-minute break: move with j and k,
// open with Enter, decide with a, e, s or d, close with Esc. The owner can switch the
// single letters off in the app's settings ("Single-letter keys"). Pure: no DOM.

export const KEY_HINT = "j / k move · Enter open · a approve · e edit · s send back · d decline · Esc close";
export const KEY_HINT_NO_LETTERS = "Enter open · Esc close";

const WHERE_TO_SWITCH = "open Settings, then Apps, then Posts, and change “Single-letter keys”";

/** What the Drafts view says under its list: {keys, note}. `letters` is the setting. */
export function keyHint(letters = true) {
  if (letters) return { keys: KEY_HINT, note: `Pressing a asks before it approves. To switch the single-letter keys off, ${WHERE_TO_SWITCH}.` };
  return { keys: KEY_HINT_NO_LETTERS, note: `The single-letter keys are off. To switch them on, ${WHERE_TO_SWITCH}.` };
}

const LETTERS = { j: "next", k: "previous", a: "approve", e: "edit", s: "send_back", d: "decline" };
const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);
// Enter opens a draft only from the page itself. On anything the owner has focused (a
// button, a link, the "checks passed" summary) Enter is that thing's own.
const NOWHERE = new Set(["", "BODY", "HTML"]);

function typingIn(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  return TYPING.has(String(target.tagName || "").toUpperCase());
}

/**
 * The action one key press means, or null to leave it to the browser.
 * `event` is {key, ctrlKey, metaKey, altKey, shiftKey, target}; `where` is
 * {view, dialogOpen, letters}. A dialog keeps its own keys, except Esc; a field being
 * typed in keeps every key, except Esc; only the Drafts view has letters, and only while
 * `letters` is not false.
 */
export function keyAction(event, where) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) return null;
  if (event.key === "Escape") return "close";
  if (where?.dialogOpen || typingIn(event.target)) return null;
  if (where?.view !== "drafts") return null;
  if (event.key === "Enter") {
    return NOWHERE.has(String(event.target?.tagName || "").toUpperCase()) ? "open" : null;
  }
  if (event.shiftKey || where?.letters === false) return null;
  return LETTERS[String(event.key)] ?? null;
}

/** The index after moving `step` places in a list of `length`, held at both ends. -1 when the list is empty. */
export function moveIndex(index, length, step) {
  if (length <= 0) return -1;
  const from = index < 0 ? (step > 0 ? -1 : length) : index;
  return Math.min(length - 1, Math.max(0, from + step));
}
