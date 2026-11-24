// The latest thing that can be undone, so the "u" key can press the Undo button
// of the toast that offers it. The toast and the key share one run-once action:
// whichever is used first does the undo, and the other then does nothing.

/** How long a toast with a button stays up (the shared toast's own figure). */
export const UNDO_LIFETIME_MS = 8000;

let latest: { run: () => void; until: number } | null = null;

/** Remembers `run` as the latest undo and returns the run-once action to put on its toast. */
export function offerUndo(run: () => void, now: number = Date.now()): () => void {
  let used = false;
  const once = () => {
    if (used) return;
    used = true;
    if (latest?.run === once) latest = null;
    run();
  };
  latest = { run: once, until: now + UNDO_LIFETIME_MS };
  return once;
}

/** Runs the latest undo if its toast is still up. Returns whether it did. */
export function undoLatest(now: number = Date.now()): boolean {
  const offer = latest;
  latest = null;
  if (!offer || now > offer.until) return false;
  offer.run();
  return true;
}
