// The wait for a reply's first text: how long it has been, and when to say more.
// A model that reasons before it answers can be quiet for minutes; the screen
// should show it is working, not hung.

/** Seconds of silence after which the second, calmer line appears. */
export const SLOW_AFTER_S = 30;
/** Below this the wait is too short to be worth counting aloud. */
export const COUNT_AFTER_S = 3;

/** Whole seconds waited, never negative, whatever the clocks did. */
export function waitedSeconds(startedAt: number, now: number): number {
  if (!Number.isFinite(startedAt) || !Number.isFinite(now)) return 0;
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}

/** "42 s" or "4 m 05 s". */
export function formatWait(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return `${whole} s`;
  return `${Math.floor(whole / 60)} m ${String(whole % 60).padStart(2, "0")} s`;
}

export type WaitNote = {
  /** The status line: "Thinking…", "Thinking… 42 s", or "Stopping…". */
  label: string;
  /** The calm second line, once the wait is long. */
  hint: string | null;
};

export const SLOW_HINT = "The model is still working. Models that reason before they answer can take a while. You can stop it, or choose a lower “effort” for this conversation.";

/**
 * What to show while a reply has not written any text.
 * `hasSteps`: the model has been looking things up, so it is plainly busy and needs no reassurance.
 */
export function waitNote(seconds: number, opts: { stopping?: boolean; hasSteps?: boolean } = {}): WaitNote {
  if (opts.stopping) return { label: "Stopping…", hint: null };
  const counted = seconds >= COUNT_AFTER_S;
  return {
    label: counted ? `Thinking… ${formatWait(seconds)}` : "Thinking…",
    hint: seconds >= SLOW_AFTER_S && !opts.hasSteps ? SLOW_HINT : null,
  };
}
