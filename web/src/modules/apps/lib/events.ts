// Which live Tape events make the apps data stale.

/** The core's own events about apps are all named `apps.<what>`; an app's own are `app.<id>.*`. */
export function staleAfter(kind: string): boolean {
  return kind.startsWith("apps.");
}

/**
 * Whether an open frame of this app should load again: a new copy of the app was put in
 * place (installed again, as `jimmyhq apps dev` does on each change, or updated). Its old
 * files are no longer served, so the frame is mounted afresh.
 */
export function reloadsFrame(appId: string, event: { kind: string; payload?: Record<string, unknown> }): boolean {
  return (event.kind === "apps.installed" || event.kind === "apps.updated") && event.payload?.app_id === appId;
}
