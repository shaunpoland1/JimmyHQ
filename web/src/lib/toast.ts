// How long a toast stays, and the countdown that waits while the owner is on it.

export type ToastKind = "info" | "success" | "error";

const LIFETIME_MS: Record<ToastKind, number> = { info: 4000, success: 4000, error: 8000 };
/** Used until the settings have been read (`appearance.action_message_seconds`). */
export const DEFAULT_ACTION_SECONDS = 20;
/** After the pointer or the keyboard leaves, a toast stays at least this long. */
export const RESUME_FLOOR_MS = 2000;

/** The seconds a toast with a button stays, from the setting's value. Anything unusable gives the default. */
export function actionSeconds(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : DEFAULT_ACTION_SECONDS;
}

/** How long a toast stays. One with something to press, such as Undo, stays long enough to reach it. */
export function lifetimeMs(kind: ToastKind, hasAction: boolean, seconds: number = DEFAULT_ACTION_SECONDS): number {
  return hasAction ? Math.max(LIFETIME_MS[kind], seconds * 1000) : LIFETIME_MS[kind];
}

/** A countdown that can wait: what is left, and since when it has been running (null while waiting). */
export type Countdown = { leftMs: number; since: number | null };

export function startCountdown(ms: number, now: number, waiting = false): Countdown {
  return { leftMs: Math.max(0, ms), since: waiting ? null : now };
}

/** What is left at `now`. A waiting countdown loses nothing. */
export function leftAt(countdown: Countdown, now: number): number {
  if (countdown.since === null) return countdown.leftMs;
  return Math.max(0, countdown.leftMs - Math.max(0, now - countdown.since));
}

export function pauseCountdown(countdown: Countdown, now: number): Countdown {
  return { leftMs: leftAt(countdown, now), since: null };
}

/** Runs again from `now`, with at least `floorMs` left so the toast does not vanish as the pointer leaves. */
export function resumeCountdown(countdown: Countdown, now: number, floorMs = RESUME_FLOOR_MS): Countdown {
  if (countdown.since !== null) return countdown;
  return { leftMs: Math.max(countdown.leftMs, floorMs), since: now };
}
