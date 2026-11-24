// Asking again until a model has answered. A model takes as long as it takes, and a page
// call is answered within about thirty seconds, so the program answers "running" at once
// and the page asks again every moment. Pure but for the timer and the check it is given.

/**
 * `check()` resolves {state: "running" | "idle" | "done", ...}. Resolves with the first
 * answer that is not "running", or null when `stillWanted()` says the owner went elsewhere.
 */
export async function untilAnswered({ check, stillWanted = () => true, every = 1500, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
  for (;;) {
    if (!stillWanted()) return null;
    const got = await check();
    if (got.state !== "running") return got;
    await sleep(every);
  }
}
