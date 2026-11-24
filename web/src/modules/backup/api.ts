// The backup module's API client. The routes are in server/jimmyhq/backup/routes.py; see
// "Back up and move your data" in docs/architecture.md. No route restores a backup: that is
// done at the computer itself, with `jimmyhq backup restore`.

import { request } from "../../api";

export type BackupMade = { ts: number; name: string; files: number; bytes: number; duration_ms: number; pruned: number };
export type BackupFailure = { ts: number; reason: string };
export type BackupTest = { ts: number; name: string | null; passed: boolean; problems: string[]; duration_ms: number };

export type BackupDestination = {
  which: "first" | "second";
  /** A folder is chosen for it. */
  set: boolean;
  last_backup: BackupMade | null;
  last_failure: BackupFailure | null;
  last_test: BackupTest | null;
  /** The standing problem with this folder, as a sentence, or null. */
  problem: string | null;
  next_ms: number | null;
};

export type BackupStatus = {
  on: boolean;
  running: boolean;
  /** When the run in progress began (unix ms), or null when none is running or it is not known. */
  running_since: number | null;
  destinations: BackupDestination[];
  problem: string | null;
  warn_after_days: number;
  now: number;
};

export type BackupStarted = { started: true; status: BackupStatus };

export const BACKUP_STATUS_KEY = ["backup", "status"] as const;

export const backupApi = {
  status: () => request<BackupStatus>("GET", "/api/backup/status"),
  /** Starts a backup in each folder set. Answers at once; the status follows. */
  now: () => request<BackupStarted>("POST", "/api/backup/now"),
  /** Starts the restore test on the newest backup in each folder set. */
  test: () => request<BackupStarted>("POST", "/api/backup/test"),
};
