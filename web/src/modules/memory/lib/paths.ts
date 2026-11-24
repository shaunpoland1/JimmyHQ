// Note paths: the same rules the server applies (server/jimmyhq/memory/paths.py),
// checked here first so a mistake is pointed out while it is being typed. The
// server still has the last word.

export const MAX_PATH = 400;
export const MAX_SEGMENT = 120;
export const MAX_SEGMENT_BYTES = 240;
export const MAX_DEPTH = 12;

const DRIVE = /^[A-Za-z]:/;
const FORBIDDEN = ["\\", ":", "*", "?", '"', "<", ">", "|"];
const DEVICE_DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "¹", "²", "³"];
const RESERVED = new Set([
  "con",
  "prn",
  "aux",
  "nul",
  "conin$",
  "conout$",
  ...DEVICE_DIGITS.map((digit) => `com${digit}`),
  ...DEVICE_DIGITS.map((digit) => `lpt${digit}`),
]);

function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

function hasControl(text: string): boolean {
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code < 32 || code === 127) return true;
  }
  return false;
}

function hasLoneSurrogate(text: string): boolean {
  for (let index = 0; index < text.length; index += 1) {
    const code = text.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = text.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) return true;
  }
  return false;
}

/**
 * Why a note path cannot be used, as one sentence, or null when it can.
 * Mirrors the server's checks in the same order.
 */
export function pathProblem(value: string): string | null {
  if (!value) return "Type a path such as shared/ideas.md.";
  if (value.length > MAX_PATH) return `A path can be at most ${MAX_PATH} characters long.`;
  if (hasLoneSurrogate(value)) return "The path contains characters that cannot be saved.";
  if (hasControl(value)) return "The path contains a control character.";
  if (value.includes("\\")) return "Use forward slashes, not backslashes.";
  if (value.startsWith("/") || value.startsWith("~")) return "The path is relative to the Library: it cannot start with / or ~.";
  if (DRIVE.test(value)) return "The path cannot start with a drive letter.";
  const bad = FORBIDDEN.filter((char) => value.includes(char));
  if (bad.length) return `The path cannot contain ${bad.join(" ")}`;
  const parts = value.split("/");
  if (parts.length > MAX_DEPTH) return `A note can be at most ${MAX_DEPTH} folders deep.`;
  for (const part of parts) {
    if (!part) return "There is an empty folder name: two slashes together, or one at an end.";
    if (part === "." || part === "..") return "A folder cannot be named . or ..";
    if (part.startsWith(".")) return "A name cannot start with a dot: hidden files are not part of the Library.";
    if (part !== part.trim() || part.endsWith(".")) return "A name cannot start or end with a space, or end with a dot.";
    if ([...part].length > MAX_SEGMENT) return `A name can be at most ${MAX_SEGMENT} characters long.`;
    if (byteLength(part) > MAX_SEGMENT_BYTES) return "A name is too long for a disk to store. Shorten it.";
    const stem = (part.split(".", 1)[0] ?? "").trim().toLowerCase();
    if (RESERVED.has(stem)) return `${part.split(".", 1)[0]} is a name Windows keeps for itself. Choose another.`;
  }
  const name = parts[parts.length - 1] ?? "";
  if (!name.toLowerCase().endsWith(".md") || name.length <= 3) return "A note's name must end in .md";
  return null;
}

export function isNotePath(value: unknown): value is string {
  return typeof value === "string" && pathProblem(value) === null;
}

/** What was typed for a new note, tidied: outer spaces off, and ".md" added when no ending was given. */
export function tidyNewPath(typed: string): string {
  const value = typed.trim();
  if (!value || value.endsWith("/")) return value;
  return value.toLowerCase().endsWith(".md") ? value : `${value}.md`;
}

function fold(name: string): string {
  return name.normalize("NFC").toLowerCase();
}

/**
 * The spelling already in the Library that a disk ignoring case, or how an
 * accented letter is stored, would treat as this path. null when it is free.
 */
export function takenBy(path: string, existing: readonly string[]): string | null {
  const wanted = fold(path);
  return existing.find((other) => fold(other) === wanted) ?? null;
}

/** The folder part of a path, "" at the top of the Library. */
export function folderOf(path: string): string {
  const at = path.lastIndexOf("/");
  return at < 0 ? "" : path.slice(0, at);
}

/** The file name, with its ending. */
export function nameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

/** The name without ".md", for a heading. */
export function titleOf(path: string): string {
  const name = nameOf(path);
  return name.toLowerCase().endsWith(".md") ? name.slice(0, -3) : name;
}

/** The first text of a new note: a heading made from its name. */
export function starterText(path: string): string {
  const words = titleOf(path).replace(/[-_]+/g, " ").trim();
  const title = words ? words.charAt(0).toUpperCase() + words.slice(1) : "Note";
  return `# ${title}\n\n`;
}

/** The note path in the screen's address, or null for none or one that could not be a note. */
export function parseMemoryRoute(sub: string | null): string | null {
  return sub !== null && isNotePath(sub) ? sub : null;
}

/** A path read back from storage: null for "none", undefined for anything unusable. */
export function parseStoredPath(stored: unknown): string | null | undefined {
  if (stored === null) return null;
  return isNotePath(stored) ? stored : undefined;
}
