// The install form: what may be typed, and the source it becomes. Each rule is a named
// function that returns a sentence for the owner, or null when the value is fine.

import type { InstallSource } from "../api.ts";

export type SourceKind = "folder" | "git";

export type InstallForm = { kind: SourceKind; path: string; url: string; commit: string };

export const EMPTY_FORM: InstallForm = { kind: "folder", path: "", url: "", commit: "" };

const COMMIT = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;
const SHORT_HASH = /^[0-9a-f]{4,39}$/;
const MAX_PATH = 1024;
const MAX_URL = 500;

/** What was typed for a commit, ready to check: trimmed and in lower case. */
export function cleanCommit(typed: string): string {
  return typed.trim().toLowerCase();
}

export function folderProblem(typed: string): string | null {
  const path = typed.trim();
  if (path === "") return "Type the path of the folder that holds the app. It has a file called jimmyhq.app.json.";
  if (path.length > MAX_PATH) return "That path is too long. Type the path of the folder itself.";
  if (/[\u0000-\u001f]/.test(path)) return "That path has a character a folder name cannot have. Type it again.";
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
    return "That is a web address, not a folder. Choose “From a git address” to install from one.";
  }
  if (/\.json$/i.test(path)) return "Give the folder, not the file in it. Remove the file name from the end.";
  return null;
}

/**
 * A git address JimmyHQ will fetch: https on any host, and nothing else. The server makes
 * the same check and is the authority; this one answers while the owner is still typing.
 * Anything that could make git run a program (`ext::`), read a local file (`file:`) or be
 * read as an option (a leading dash) is refused, and so is a name or password written into
 * the address, because the address is kept in the record.
 */
export function gitUrlProblem(typed: string): string | null {
  const url = typed.trim();
  const https = "A git address starts with https://. Copy it again from where you found it.";
  if (url === "") return "Type the git address of the app, such as https://git.example.com/apps/water.git.";
  if (url.length > MAX_URL) return "That address is too long. Check it is the address of one repository.";
  if (/\s/.test(url) || /[\u0000-\u001f]/.test(url)) return "A git address has no spaces in it. Copy it again from where you found it.";
  if (url.startsWith("-")) return https;
  if (url.includes("::") || /^(?:ext|file|fd|transport):/i.test(url)) {
    return "That kind of address is not allowed. Use an https:// address, or install from a folder instead.";
  }
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(url);
  if (!scheme) {
    if (/^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:/.test(url)) {
      return "That is an ssh address. JimmyHQ fetches apps over https only: use the repository's https:// address.";
    }
    return https;
  }
  const name = (scheme[1] ?? "").toLowerCase();
  if (name !== "https") return `Addresses that start with ${name}:// are not allowed. Use the repository's https:// address.`;
  const rest = url.slice(scheme[0].length);
  const host = rest.split("/")[0] ?? "";
  if (host === "" || host.startsWith("@")) return "That address has no host name. Check it and type it again.";
  if (host.includes("@")) {
    return "Leave the user name and password out of the address. JimmyHQ never stores them in an app's source.";
  }
  if (/[?#]/.test(rest)) return "Leave off everything from the ? or # onwards. JimmyHQ needs only the repository's address.";
  return null;
}

export function commitProblem(typed: string): string | null {
  const commit = cleanCommit(typed);
  if (commit === "") return "Type the exact commit to install: the full hash, 40 characters. The app is pinned to it.";
  if (COMMIT.test(commit)) return null;
  if (SHORT_HASH.test(commit)) {
    return "That is a short hash, and a short hash can match more than one commit. Type the full 40 characters.";
  }
  if (/^[0-9a-f]+$/.test(commit)) return "A commit hash is 40 characters. This one is the wrong length.";
  return "A commit hash is made of the digits 0 to 9 and the letters a to f, 40 of them. A branch or tag name will not do: it can move.";
}

export type FormProblems = { path: string | null; url: string | null; commit: string | null };

export function formProblems(form: InstallForm): FormProblems {
  return form.kind === "folder"
    ? { path: folderProblem(form.path), url: null, commit: null }
    : { path: null, url: gitUrlProblem(form.url), commit: commitProblem(form.commit) };
}

export function formReady(form: InstallForm): boolean {
  const problems = formProblems(form);
  return problems.path === null && problems.url === null && problems.commit === null;
}

/** The source the form describes, or null while any part of it is not fine. */
export function formSource(form: InstallForm): InstallSource | null {
  if (!formReady(form)) return null;
  return form.kind === "folder"
    ? { kind: "folder", path: form.path.trim() }
    : { kind: "git", url: form.url.trim(), commit: cleanCommit(form.commit) };
}

/** A commit shown short, for a line of text; the full hash stays in a title. */
export function shortCommit(commit: string | null | undefined): string {
  return commit ? commit.slice(0, 10) : "";
}

/** Whether what was typed matches the app's name, for the apps that ask for it. */
export function nameMatches(typed: string, name: string): boolean {
  const wanted = name.trim().toLowerCase();
  return wanted !== "" && typed.trim().toLowerCase() === wanted;
}
