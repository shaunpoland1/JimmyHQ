// Logic for the Backups settings card. No browser or React in here.

import type { BackupDestination, BackupStatus } from "../api.ts";

const NAMES: Readonly<Record<BackupDestination["which"], string>> = { first: "Backup folder", second: "Second backup folder" };

export function destinationName(which: BackupDestination["which"]): string {
  return NAMES[which];
}

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago". */
export function ago(ts: number, now: number): string {
  const seconds = Math.max(0, Math.round((now - ts) / 1000));
  if (seconds < 60) return "just now";
  const units: [number, string][] = [[86_400, "day"], [3_600, "hour"], [60, "minute"]];
  for (const [size, unit] of units) {
    if (seconds >= size) {
      const count = Math.floor(seconds / size);
      return `${count} ${unit}${count === 1 ? "" : "s"} ago`;
    }
  }
  return "just now";
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

export type Tone = "ok" | "warn" | "off";

/** The lines shown for one folder: its last backup, its last failure if newer, its last test. */
export function destinationLines(destination: BackupDestination, now: number): { tone: Tone; lines: string[] } {
  if (!destination.set) return { tone: "off", lines: ["No folder chosen."] };
  const lines: string[] = [];
  const made = destination.last_backup;
  const failed = destination.last_failure;
  const tested = destination.last_test;
  if (made) lines.push(`Last backup ${ago(made.ts, now)}: ${made.files} files, ${formatSize(made.bytes)}.`);
  else if (!failed) lines.push("No backup yet. The first one is made within a few minutes.");
  if (failed && (!made || failed.ts > made.ts)) lines.push(`The last try ${ago(failed.ts, now)} failed: ${failed.reason}`);
  if (tested) {
    lines.push(tested.passed ? `Restore test passed ${ago(tested.ts, now)}.` : `Restore test failed ${ago(tested.ts, now)}: ${tested.problems[0] ?? "see the Tape."}`);
  } else if (made) {
    lines.push("Not tested yet beyond the quick check each backup gets.");
  }
  return { tone: destination.problem ? "warn" : "ok", lines };
}

/** The headline for the whole card. */
export function headline(status: BackupStatus): { tone: Tone; label: string } {
  if (status.running) return { tone: "ok", label: "Working…" };
  if (!status.on) return { tone: "off", label: "Off" };
  if (status.problem) return { tone: "warn", label: "Needs a look" };
  return { tone: "ok", label: "On" };
}

/** `{ timeZone }` for Intl when a zone is named and this browser knows it; otherwise local time. */
function zoneOption(timeZone?: string): { timeZone?: string } {
  if (!timeZone || timeZone === "local") return {};
  try {
    new Intl.DateTimeFormat("en-CA", { timeZone });
    return { timeZone };
  } catch {
    return {};
  }
}

/** A moment in the owner's time zone: the time of day for today, the day and time before that. */
export function sinceTime(ts: number, now: number, timeZone?: string, locale?: string): string {
  const zone = zoneOption(timeZone);
  const day = (at: number) => new Intl.DateTimeFormat("en-CA", { ...zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(at));
  const clock = { hour: "numeric", minute: "2-digit" } as const;
  if (day(ts) === day(now)) return new Intl.DateTimeFormat(locale, { ...zone, ...clock }).format(new Date(ts));
  return new Intl.DateTimeFormat(locale, { ...zone, weekday: "short", month: "short", day: "numeric", ...clock }).format(new Date(ts));
}

const HOUR_MS = 3_600_000;

/** The line shown while a backup or test is running, and whether it has gone on longer than the owner allows. */
export function runningLine(
  status: Pick<BackupStatus, "running" | "running_since" | "now">,
  options: { stuckAfterHours: number | null; timeZone?: string; locale?: string },
): { stuck: boolean; text: string } | null {
  if (!status.running) return null;
  const since = status.running_since;
  if (since === null) return { stuck: false, text: "A backup is running." };
  const when = `${sinceTime(since, status.now, options.timeZone, options.locale)} (${ago(since, status.now)})`;
  const limit = options.stuckAfterHours;
  if (limit !== null && limit > 0 && status.now - since > limit * HOUR_MS) {
    return {
      stuck: true,
      text: `A backup has been running since ${when}, longer than the ${limit} hours set for Backups. It may be stuck. The card on the Bench says what to do.`,
    };
  }
  return { stuck: false, text: `A backup has been running since ${when}.` };
}

type FeedEvent = { kind: string; payload?: Record<string, unknown> };

/** True for a tape event after which the status may read differently. */
export function affectsBackupStatus(event: FeedEvent): boolean {
  if (event.kind.startsWith("backup.")) return true;
  if (event.kind === "setting.updated" || event.kind === "setting.reset") {
    const key = event.payload?.key;
    return typeof key === "string" && key.startsWith("backup.");
  }
  return false;
}
