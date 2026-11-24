// Letting go of the replies this tab is listening to, all at once: what
// happens when the session ends.

export type EndableRun = {
  abort: AbortController;
  buffer: string;
  frame: number | null;
  /** An ending without `done` is expected, so nothing is reported. */
  stopped: boolean;
  /** The session it belonged to has ended: it must not touch the screen again. */
  dropped: boolean;
};

/**
 * Ends every run: its connection is closed, its pending redraw cancelled and
 * its unshown text thrown away, and it is marked so that whatever is still
 * reading it stays silent. The map is left empty.
 */
export function endRuns(runs: Map<string, EndableRun>, cancelFrame: (frame: number) => void): void {
  for (const run of runs.values()) {
    run.stopped = true;
    run.dropped = true;
    if (run.frame !== null) cancelFrame(run.frame);
    run.frame = null;
    run.buffer = "";
    run.abort.abort();
  }
  runs.clear();
}
