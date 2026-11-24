// Where one app's detail view lives in the address: `#/apps/<id>`.

/** The app id in `#/apps/<id>`, from the route's sub-path, or null for the list. */
export function appIdFromSub(sub: string | null): string | null {
  if (!sub) return null;
  const id = sub.split("/")[0] ?? "";
  return /^[a-z][a-z0-9_]{1,30}$/.test(id) ? id : null;
}

/** The app open on the Apps screen. Another screen's address (Settings, shown over
 * this one) says nothing about it, so what was open stays open. */
export function openAppId(route: { screen: string; sub: string | null }, was: string | null): string | null {
  return route.screen === "apps" ? appIdFromSub(route.sub) : was;
}

export function detailHash(id: string): string {
  return `#/apps/${encodeURIComponent(id)}`;
}

export const LIST_HASH = "#/apps";

/** The hash of the settings section holding an app's settings. */
export function settingsHash(app: { id: string; settings_section?: string | null }): string {
  return `#/settings/${encodeURIComponent(app.settings_section ?? `app.${app.id}`)}`;
}

/** The hash of the settings section that holds the rules about apps themselves. */
export const APPS_SETTINGS_HASH = "#/settings/apps";

/** The hash of an app's own screen, `#/app/<id>/<screen>`: the one form the shell routes. */
export function screenHash(appId: string, screenId: string): string {
  return `#/app/${encodeURIComponent(appId)}/${encodeURIComponent(screenId)}`;
}
