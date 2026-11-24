// The composer's small rules: what may be sent, the character counter,
// putting dictated words at the cursor, and reading a refusal.

/** Length as the server counts it: characters, not UTF-16 units. */
export function countChars(text: string): number {
  let count = 0;
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    // The second half of a pair belongs to the character before it.
    if (code >= 0xdc00 && code <= 0xdfff && index > 0) {
      const before = text.charCodeAt(index - 1);
      if (before >= 0xd800 && before <= 0xdbff) continue;
    }
    count += 1;
  }
  return count;
}

/** The counter appears once this share of the limit is used. */
const COUNTER_FROM = 0.9;

export type Counter = { used: number; limit: number; over: boolean };

/** The counter to show, or null while the message is nowhere near the limit. */
export function counterFor(text: string, limit: number): Counter | null {
  if (!Number.isFinite(limit) || limit <= 0) return null;
  // UTF-16 length is never less than the character count, so this is a cheap way out.
  if (text.length < limit * COUNTER_FROM) return null;
  const used = countChars(text);
  if (used < limit * COUNTER_FROM) return null;
  return { used, limit, over: used > limit };
}

export function counterText(counter: Counter, locale?: string): string {
  const number = new Intl.NumberFormat(locale);
  return `${number.format(counter.used)} / ${number.format(counter.limit)}`;
}

/** A message can be sent when it has something printable and fits the limit. */
export function canSend(text: string, limit: number): boolean {
  if (text.trim() === "") return false;
  return !(Number.isFinite(limit) && limit > 0 && text.length > limit && countChars(text) > limit);
}

export type Insertion = { text: string; cursor: number };

/**
 * Puts dictated words where the cursor is (replacing a selection), with a
 * space on either side where one is needed to keep words apart.
 */
export function insertAtCursor(text: string, start: number, end: number, insert: string): Insertion {
  const from = Math.max(0, Math.min(start, end, text.length));
  const to = Math.max(from, Math.min(Math.max(start, end), text.length));
  const words = insert.trim();
  if (!words) return { text, cursor: to };
  const before = text.slice(0, from);
  const after = text.slice(to);
  const lead = before !== "" && !/\s$/.test(before) ? " " : "";
  const trail = after !== "" && !/^\s/.test(after) ? " " : "";
  const placed = `${lead}${words}${trail}`;
  return { text: `${before}${placed}${after}`, cursor: before.length + lead.length + words.length };
}

/** The sentence in a refusal's JSON body, as the shared client reads it. */
export function refusalDetail(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : ""))
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
  }
  return `Request failed (${status}).`;
}

/**
 * Whether a refused send is one the owner fixes under Settings, Models and
 * roles: the 409 that says no model (or no local model) is set up.
 */
export function offersModelSettings(status: number, detail: string): boolean {
  return status === 409 && /models and roles/i.test(detail);
}

export type ComposerKey = {
  key: string;
  shiftKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  /** True while an input method is still composing a character. */
  composing?: boolean;
  /** True for the repeats a held key makes. */
  repeat?: boolean;
};

/** What a key means in the composer: Enter sends (once per press), Shift+Enter is a new line, Escape stops a reply. */
export function composerCommand(press: ComposerKey, streaming: boolean): "send" | "stop" | "ignore" | null {
  if (press.composing) return null;
  if (press.key === "Escape") return streaming ? "stop" : null;
  if (press.key !== "Enter") return null;
  if (press.shiftKey || press.altKey) return null;
  // One press sends once. A held Enter does nothing more: it neither sends again when the
  // words come back after a refusal, nor adds lines.
  if (press.repeat) return "ignore";
  return "send";
}

export type ScreenKey = {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  repeat?: boolean;
  /** Focus is in a field, so the key is text. */
  typing: boolean;
  /** A dialog is open over the screen. */
  dialogOpen: boolean;
};

const TEXT_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const TEXT_ROLES = new Set(["listbox", "menu", "combobox", "textbox", "searchbox", "tree", "grid"]);

/**
 * Whether a key pressed where the focus is belongs to what is focused: a
 * field, or a list or menu that finds its items by the letters typed (the
 * model picker's list does). `widgetRole` is the role of the nearest such
 * widget the focus is in, if any.
 */
export function focusTakesKeys(tag: string, editable: boolean, widgetRole: string | null): boolean {
  if (editable || TEXT_TAGS.has(tag.toUpperCase())) return true;
  return widgetRole !== null && TEXT_ROLES.has(widgetRole.toLowerCase());
}

/** The selector for the widgets `focusTakesKeys` knows by role. */
export const KEY_WIDGET_SELECTOR = [...TEXT_ROLES].map((role) => `[role="${role}"]`).join(",");

/** The chat screen's single keys: n starts a conversation, / goes to the search box. */
export function screenCommand(press: ScreenKey): "new" | "search" | null {
  if (press.typing || press.dialogOpen || press.repeat) return null;
  if (press.ctrlKey || press.metaKey || press.altKey) return null;
  if (press.key === "/") return "search";
  if (press.key.toLowerCase() === "n") return "new";
  return null;
}
