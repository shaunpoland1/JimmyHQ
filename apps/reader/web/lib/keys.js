// The keys of the Triage view, shaped for a five-minute break: y keep, l read later,
// x skip, o send on, m mine, j for the next article and k for the one before, Esc to
// close. j and k move, as they do in Posts; no letter that approves or sends back there
// (a, s) changes anything here. The arrow keys, Space and the page keys are the
// browser's, so they scroll. The owner can switch the letters off. Pure: no DOM.

/** The letter for each choice. The buttons show these, so the two cannot drift. */
export const KEY_OF = { keep: "y", later: "l", skip: "x", send: "o", mine: "m" };

export const KEY_HINT = "y keep · l read later · x skip · o send on · m mine · j next · k previous · Esc close";
export const KEYS_ON_NOTE = "The arrow keys scroll. You can switch the letter keys off in Reader's settings: “Single-letter keys in Triage”.";
export const KEYS_OFF_HINT = "The letter keys are off; Esc still closes. To use them, switch on “Single-letter keys in Triage” in Reader's settings.";

/** The lines under the choices: the keys and where to switch them off, or that they are off. */
export function keyHint(lettersOn) {
  return lettersOn ? [KEY_HINT, KEYS_ON_NOTE] : [KEYS_OFF_HINT];
}

const LETTERS = { ...Object.fromEntries(Object.entries(KEY_OF).map(([action, key]) => [key, action])), j: "next", k: "previous" };
const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function typingIn(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  return TYPING.has(String(target.tagName || "").toUpperCase());
}

/**
 * The action one key press means, or null to leave it to the browser.
 * `event` is {key, ctrlKey, metaKey, altKey, shiftKey, target}; `where` is
 * {view, dialogOpen, lettersOff}. A dialog keeps its own keys, except Esc; a field being
 * typed in keeps every key, except Esc; only the Triage view has letters, and none when
 * the owner switched them off.
 */
export function keyAction(event, where) {
  if (!event || event.ctrlKey || event.metaKey || event.altKey) return null;
  if (event.key === "Escape") return "close";
  if (where?.dialogOpen || typingIn(event.target)) return null;
  if (where?.view !== "triage" || where?.lettersOff) return null;
  if (event.shiftKey) return null;
  return Object.hasOwn(LETTERS, String(event.key)) ? LETTERS[String(event.key)] : null;
}

/** The choices that are the owner's vote on an article, and close its card. */
export const VOTES = new Set(["keep", "later", "skip", "send"]);

/** The index after moving `step` places in a list of `length`, held at both ends. -1 when the list is empty. */
export function moveIndex(index, length, step) {
  if (length <= 0) return -1;
  const from = index < 0 ? (step > 0 ? -1 : length) : index;
  return Math.min(length - 1, Math.max(0, from + step));
}

/** Where Triage stands after the article at `index` was decided and left the list. */
export function afterDecided(index, lengthBefore) {
  const left = lengthBefore - 1;
  if (left <= 0) return -1;
  return Math.min(index, left - 1);
}
