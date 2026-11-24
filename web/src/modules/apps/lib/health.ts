// How a full app's program is doing, in plain words, for the Apps screen: running,
// stopped, held back after crashing, not responding. Pure: the health panel only draws what
// this says. The server's words are in server/jimmyhq/apps/runtime.py (`health`) and
// server/jimmyhq/apps/plugs/jobs.py (`status`).

import type { Tone } from "./status.ts";

export const HEALTH_STATES = ["running", "starting", "restarting", "not_responding", "crashing", "stopped", "not_running"] as const;
export type HealthState = (typeof HEALTH_STATES)[number];

export type JobRun = {
  name: string;
  everyS: number;
  lastRun: number | null;
  finished: number | null;
  ok: boolean | null;
  error: string | null;
  running: boolean;
};

export type Health = {
  state: HealthState;
  startedAt: number | null;
  /** When the program last asked for work, unix ms. */
  lastBeat: number | null;
  /** When it was started, newest last, unix ms. */
  restarts: number[];
  lastExit: { code: number | null; at: number | null } | null;
  failures: number;
  log: string[];
  jobs: JobRun[];
};

type Bag = Record<string, unknown>;

function bag(value: unknown): Bag | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Bag) : null;
}

function ms(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function healthState(value: unknown): HealthState | null {
  return typeof value === "string" && (HEALTH_STATES as readonly string[]).includes(value) ? (value as HealthState) : null;
}

/** The answer of GET /api/apps/<id>/health. Anything unexpected is left out, never guessed. */
export function readHealth(raw: unknown): Health {
  const body = bag(raw) ?? {};
  const exit = bag(body.last_exit);
  const jobs = Array.isArray(body.jobs) ? body.jobs : [];
  return {
    state: healthState(body.state) ?? "not_running",
    startedAt: ms(body.started_at),
    lastBeat: ms(body.last_beat),
    restarts: Array.isArray(body.restarts) ? body.restarts.filter((t): t is number => ms(t) !== null) : [],
    lastExit: exit ? { code: typeof exit.code === "number" ? exit.code : null, at: ms(exit.at) } : null,
    failures: ms(body.failures) ?? 0,
    log: Array.isArray(body.log) ? body.log.filter((line): line is string => typeof line === "string").slice(-200) : [],
    jobs: jobs.flatMap((item) => {
      const job = bag(item);
      if (!job || typeof job.name !== "string") return [];
      return [
        {
          name: job.name,
          everyS: ms(job.every_s) ?? 0,
          lastRun: ms(job.last_run),
          finished: ms(job.finished),
          ok: typeof job.ok === "boolean" ? job.ok : null,
          error: typeof job.error === "string" ? job.error : null,
          running: job.running === true,
        },
      ];
    }),
  };
}

export type HealthWords = { label: string; tone: Tone; sentence: string };

export const HEALTH_WORDS: Record<HealthState, HealthWords> = {
  running: { label: "Running", tone: "ok", sentence: "Its program is running and asking JimmyHQ for work." },
  starting: { label: "Starting", tone: "none", sentence: "Its program is starting. This takes a few seconds." },
  restarting: { label: "Restarting", tone: "warn", sentence: "Its program stopped and is about to be started again." },
  not_responding: {
    label: "Not responding",
    tone: "warn",
    sentence: "Its program is running but has not asked JimmyHQ for work for a while. Restart it to start it afresh.",
  },
  crashing: {
    label: "Held back after crashing",
    tone: "bad",
    sentence: "Its program kept stopping soon after it started, so JimmyHQ stopped starting it. It is still switched on and its data is untouched. Its last lines below may say why. Press Restart to start it again.",
  },
  stopped: { label: "Stopped", tone: "none", sentence: "It is switched off, so its program is not running." },
  not_running: {
    label: "Not running",
    tone: "warn",
    sentence: "JimmyHQ cannot tell whether its program is running: the part of JimmyHQ that starts programs has not said so lately.",
  },
};

export function healthWords(state: HealthState): HealthWords {
  return HEALTH_WORDS[state];
}

/** Restart is offered for a switched-on full app, whatever its program is doing. */
export function canRestart(app: { tier: string; enabled: boolean }): boolean {
  return app.tier === "full" && app.enabled;
}

/** The toast after Restart. */
export function restartedToast(name: string, was: HealthState): string {
  return was === "crashing" ? `Starting ${name} again. It was held back after crashing.` : `Restarting ${name}.`;
}

/** How many times the program was started in the last day. */
export function startsInLastDay(restarts: readonly number[], now: number): number {
  const since = now - 24 * 60 * 60 * 1000;
  return restarts.filter((at) => at >= since && at <= now).length;
}

/** How often a job runs, in words: "every 5 minutes", "every hour". */
export function everyText(seconds: number): string {
  if (seconds <= 0) return "on its own schedule";
  const units: [number, string][] = [
    [86400, "day"],
    [3600, "hour"],
    [60, "minute"],
  ];
  for (const [size, word] of units) {
    if (seconds >= size && seconds % size === 0) {
      const n = seconds / size;
      return n === 1 ? `every ${word}` : `every ${n} ${word}s`;
    }
  }
  return seconds === 1 ? "every second" : `every ${seconds} seconds`;
}

/** One job's last run, in a short sentence. `when` formats a time at the display edge. */
export function jobLine(job: JobRun, when: (ms: number) => string): string {
  const every = everyText(job.everyS);
  if (job.running) return `Running now (${every}).`;
  if (job.lastRun === null) return `Has not run yet (${every}).`;
  const at = when(job.finished ?? job.lastRun);
  if (job.ok === false) return `Last run ${at} did not work: ${job.error ?? "the app did not say why."}`;
  return `Last ran ${at} (${every}).`;
}
