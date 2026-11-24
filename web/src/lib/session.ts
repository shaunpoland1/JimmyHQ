// Things to do when the signed-in session ends (sign-out or an expired session).
//
// A module that keeps work alive outside React, such as a reply still being
// written, registers a function here so nothing from one session lingers
// into the next.

const hooks = new Set<() => void>();

/** Registers a function to run when the session ends. Returns the function that removes it. */
export function onSessionEnd(hook: () => void): () => void {
  hooks.add(hook);
  return () => {
    hooks.delete(hook);
  };
}

/** Called by the app when the session ends. One failing hook does not stop the rest. */
export function runSessionEndHooks(): void {
  hooks.forEach((hook) => {
    try {
      hook();
    } catch {
      // Clearing up is best effort.
    }
  });
}
