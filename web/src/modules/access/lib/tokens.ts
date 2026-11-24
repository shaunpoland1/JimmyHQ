// What the API tokens list and its dialogs say, kept apart from the screen so it can be tested.

import type { ApiToken, TokenLevel } from "../api.ts";

/** The same limit the server holds a label to. */
export const MAX_TOKEN_LABEL = 64;

export const LEVELS: readonly { value: TokenLevel; label: string; help: string }[] = [
  { value: "reader", label: "Reader", help: "Can read what JimmyHQ holds. Cannot change anything." },
  { value: "writer", label: "Writer", help: "Can read and make changes, such as adding a task. Cannot change settings, passwords or tokens." },
];

export function levelLabel(level: TokenLevel): string {
  return LEVELS.find((entry) => entry.value === level)?.label ?? level;
}

/** What is wrong with a label before it is sent, or null when it can be sent. */
export function labelProblem(label: string): string | null {
  const trimmed = label.trim();
  if (!trimmed) return "Give the token a label, such as the screen or script that will use it.";
  if (trimmed.length > MAX_TOKEN_LABEL) return `Keep the label to ${MAX_TOKEN_LABEL} characters or fewer.`;
  if (trimmed.includes(":")) return "Leave out the colon.";
  if (/[\u0000-\u001f\u007f]/.test(trimmed)) return "Use letters, digits, spaces and punctuation only.";
  return null;
}

/** Live tokens first, newest first; revoked ones after, newest revoked first. */
export function ordered(tokens: readonly ApiToken[]): { live: ApiToken[]; revoked: ApiToken[] } {
  const live = tokens.filter((t) => t.revoked === null).sort((a, b) => b.created - a.created);
  const revoked = tokens.filter((t) => t.revoked !== null).sort((a, b) => (b.revoked ?? 0) - (a.revoked ?? 0));
  return { live, revoked };
}

/** The line under the section heading. */
export function countLine(tokens: readonly ApiToken[]): string {
  const live = tokens.filter((t) => t.revoked === null).length;
  if (live === 0) return "No token is in use. Scripts and screens can only reach JimmyHQ with your password.";
  return live === 1 ? "1 token is in use." : `${live} tokens are in use.`;
}

/** What the revoke confirmation says. */
export function revokeMessage(token: ApiToken): string {
  return `Anything using “${token.label}” stops working on its next request, including a live feed it has open. This cannot be undone: to let it back in, make a new token.`;
}

export function madeLine(token: ApiToken): string {
  return `The ${levelLabel(token.level).toLowerCase()} token “${token.label}” was made.`;
}

export function revokedLine(token: ApiToken): string {
  return `The token “${token.label}” was revoked. It no longer works.`;
}
