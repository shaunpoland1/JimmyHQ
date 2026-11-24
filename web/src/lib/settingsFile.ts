// Settings as files: reading what the owner picked or pasted, and wording what
// an import would do. The server does the real checking; this keeps the screen
// from sending something that is plainly not a settings file.

import type { PlanEntry, Setting, SettingsDocument, TapeEvent } from "../api.ts";
import { describeValue } from "./settings.ts";

export type ParsedDocument = { ok: true; document: SettingsDocument } | { ok: false; error: string };

/** Largest file the server accepts, in bytes. Mirrors the server's limit. */
export const MAX_SETTINGS_FILE_BYTES = 256 * 1024;

/** The commands shown under the Settings file buttons. */
export const SETTINGS_CLI_HELP = [
  "jimmyhq settings list --changed",
  "jimmyhq settings set <key> <value>",
  "jimmyhq settings export --profile -o profile.json",
  "jimmyhq settings import profile.json --dry-run",
] as const;

/** Turns picked or pasted text into a document, or says what is wrong with it. */
export function parseSettingsText(text: string): ParsedDocument {
  const trimmed = text.replace(/^﻿/, "").trim();
  if (!trimmed) return { ok: false, error: "Choose a settings file, or paste its contents." };
  if (trimmed.length > MAX_SETTINGS_FILE_BYTES) {
    return { ok: false, error: "That is too large to be a settings file. Check that it is the right file." };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return { ok: false, error: "That is not valid JSON. Check it for a missing comma or quote." };
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed) || !("jimmyhq_settings" in parsed)) {
    return {
      ok: false,
      error: 'This is not a JimmyHQ settings file: it has no "jimmyhq_settings" line. Export one to see an example.',
    };
  }
  return { ok: true, document: parsed as SettingsDocument };
}

/** "1 setting changed." / "3 settings changed." / "No settings changed." */
export function changedMessage(count: number): string {
  if (count === 0) return "No settings changed.";
  return `${count} ${count === 1 ? "setting" : "settings"} changed.`;
}

export type ChangeLine = { key: string; label: string; from: string; to: string };

/** One change as "label: old → new", worded with the setting's own choices. */
export function changeLine(entry: PlanEntry, schema: readonly Setting[]): ChangeLine {
  const setting = schema.find((item) => item.key === entry.key);
  const shape = setting ?? { type: "str" as const, choices: undefined };
  const to = describeValue(shape, entry.new);
  return {
    key: entry.key,
    label: entry.label ?? entry.key,
    from: describeValue(shape, entry.old),
    to: entry.reset ? `${to} (default)` : to,
  };
}

/** The newest event is a settings change that this screen has not shown yet. */
export function settingsChangedSince(events: readonly Pick<TapeEvent, "seq" | "kind">[], afterSeq: number): boolean {
  return events.some((event) => event.seq > afterSeq && (event.kind === "setting.updated" || event.kind === "setting.reset"));
}
