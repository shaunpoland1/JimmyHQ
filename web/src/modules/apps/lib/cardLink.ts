// Where a card an app filed points: the app's own page. A private app's card carries a
// title only, so the Bench says where to go by itself.
import type { InstalledApp } from "./installed.ts";
import { screenHash } from "./route.ts";

export type CardAppLink = { label: string; href: string };

/** The "Open <app>" link for a card filed by `app:<id>`, or null when the card is not an app's, the app is not installed, it is switched off, or it has no screen. */
export function cardAppLink(filedBy: string, apps: readonly InstalledApp[]): CardAppLink | null {
  if (!filedBy.startsWith("app:")) return null;
  const id = filedBy.slice("app:".length);
  const app = apps.find((candidate) => candidate.id === id);
  const first = app?.screens[0];
  if (!app || !app.enabled || !first) return null;
  return { label: `Open ${app.name}`, href: screenHash(app.id, first.id) };
}
