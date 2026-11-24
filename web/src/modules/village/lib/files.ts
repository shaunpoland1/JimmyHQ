// Browsing an agent's folder and the shared folders: paths, breadcrumbs, and the words
// around a file that is only partly shown.

import type { FileItem, FileList, FileText } from "../api.ts";
import { labelForId } from "./names.ts";

/** Whose folder is being browsed. */
export type Scope = "own" | "shared";

/** A path inside a folder, without a leading slash or empty parts. */
export function cleanPath(path: string): string {
  return path
    .replace(/\\/g, "/")
    .split("/")
    .filter((part) => part !== "" && part !== ".")
    .join("/");
}

export function joinPath(base: string, name: string): string {
  return cleanPath(`${base}/${name}`);
}

/** The folder above, "" at the top. */
export function parentPath(path: string): string {
  const parts = cleanPath(path).split("/").filter(Boolean);
  return parts.slice(0, -1).join("/");
}

export function baseName(path: string): string {
  const parts = cleanPath(path).split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

export type Crumb = { label: string; path: string };

/** The trail from the top folder to `path`: each part is a place to go back to. */
export function breadcrumbs(path: string, rootLabel: string): Crumb[] {
  const trail: Crumb[] = [{ label: rootLabel, path: "" }];
  let at = "";
  for (const part of cleanPath(path).split("/").filter(Boolean)) {
    at = joinPath(at, part);
    trail.push({ label: part, path: at });
  }
  return trail;
}

export function isMarkdownName(name: string): boolean {
  return /\.(md|markdown|mdown)$/i.test(name);
}

/** A listing answers with `items`; a file with `text`. The shared route sends either. */
export function isFileText(answer: FileList | FileText): answer is FileText {
  return typeof (answer as FileText).text === "string";
}

/** Text that decoded with many replacement marks or holds control characters is a binary file. */
export function looksBinary(text: string): boolean {
  if (text.length === 0) return false;
  const sample = text.slice(0, 4000);
  let odd = 0;
  for (const char of sample) {
    const code = char.codePointAt(0) ?? 0;
    if (code === 0xfffd || (code < 32 && code !== 9 && code !== 10 && code !== 13 && code !== 12)) odd += 1;
  }
  return odd / sample.length > 0.02;
}

/** How much of a file arrived, in bytes of the text received. */
export function receivedBytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

/** The sentence about a file the server cut short, or null when all of it is here. */
export function partialNote(file: Pick<FileText, "truncated" | "size" | "text">, formatSize: (bytes: number) => string): string | null {
  if (!file.truncated) return null;
  const shown = formatSize(receivedBytes(file.text));
  const whole = typeof file.size === "number" ? ` of ${formatSize(file.size)}` : "";
  return `This file is too large to show in full: the first ${shown}${whole} is shown and the rest is cut off. To read all of it, open the file from the agent's folder on disk.`;
}

/** The listing the server sent may be capped; say what is listed. */
export function folderSummary(items: readonly FileItem[]): string {
  const folders = items.filter((item) => item.dir).length;
  const files = items.length - folders;
  if (items.length === 0) return "Empty";
  const part = (count: number, one: string, many: string) => (count === 0 ? null : `${count} ${count === 1 ? one : many}`);
  return [part(folders, "folder", "folders"), part(files, "file", "files")].filter(Boolean).join(", ");
}

/** Folders first, then files, each by name. The server already sends this order; this keeps it if it does not. */
export function sortItems(items: readonly FileItem[]): FileItem[] {
  return [...items].sort((a, b) => Number(b.dir) - Number(a.dir) || a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
}

/** The address that saves one file (`zip` false) or a folder as a zip (`zip` true). A plain link to it downloads. */
export function downloadHref(scope: Scope, agentId: string, path: string, zip: boolean): string {
  const kind = zip ? "zip" : "download";
  const base = scope === "own" ? `/api/village/agents/${encodeURIComponent(agentId)}/files/${kind}` : `/api/village/shared/${kind}`;
  return `${base}?path=${encodeURIComponent(cleanPath(path))}`;
}

/** The name a zip of this folder is saved as is chosen by the server; this is only the words on the button. */
export function zipLabel(folderPath: string): string {
  return folderPath ? "Download this folder as a zip" : "Download all as a zip";
}

export function rootLabel(scope: Scope, name: string): string {
  return scope === "own" ? `${name}'s files` : "Shared folders";
}

type Who = Parameters<typeof labelForId>[1][number];

/**
 * What a row or crumb is called on screen. The top-level folders of the shared area are one
 * per agent and named by its id, so they show the agent's label; everything else is shown as it is.
 */
export function shownName(scope: Scope, parentDir: string, name: string, everyone: readonly Who[]): string {
  return scope === "shared" && cleanPath(parentDir) === "" ? labelForId(name, everyone) : name;
}

/** The trail with the agent folder at the top of the shared area named by its label. */
export function labelledCrumbs(scope: Scope, crumbs: readonly Crumb[], everyone: readonly Who[]): Crumb[] {
  return crumbs.map((crumb, index) => (scope === "shared" && index === 1 ? { ...crumb, label: labelForId(crumb.label, everyone) } : crumb));
}
