// Hash routing: the current screen lives in `location.hash`, e.g. `#/bench`
// or `#/settings/appearance`. No router library. An installed app's screen is
// `#/app/<id>/<screen>`: the screen is `app` and the rest is its sub-route.

import { DEFAULT_SCREEN, isScreenId } from "./screens.ts";
import type { ScreenId } from "./screens.ts";

export type Route = { screen: ScreenId; sub: string | null };

export const DEFAULT_ROUTE: Route = { screen: DEFAULT_SCREEN, sub: null };

function safeDecode(part: string): string {
  try {
    return decodeURIComponent(part);
  } catch {
    return part;
  }
}

export function parseHash(hash: string): Route {
  const parts = hash
    .replace(/^#/, "")
    .split("/")
    .filter((part) => part.length > 0)
    .map(safeDecode);
  const first = parts[0]?.toLowerCase();
  if (first === undefined || !isScreenId(first)) return DEFAULT_ROUTE;
  // Everything after the screen is the sub-route, so a path such as a note's
  // (`#/memory/shared/ideas.md`) reads naturally and survives being typed by hand.
  return { screen: first, sub: parts.length > 1 ? parts.slice(1).join("/") : null };
}

/**
 * The hash a plain path stands for: `/settings` is `#/settings`, `/app/<id>/<screen>` is
 * `#/app/<id>/<screen>`. Null when the address already has a hash, or its path is not a
 * screen (the root, or something this app does not know), so nothing is moved.
 */
export function hashForPath(pathname: string, hash: string): string | null {
  if (hash !== "" && hash !== "#") return null;
  const first = pathname.split("/").find((part) => part.length > 0);
  if (first === undefined || !isScreenId(safeDecode(first).toLowerCase())) return null;
  return formatHash(parseHash(pathname));
}

export function formatHash(route: Route): string {
  if (!route.sub) return `#/${route.screen}`;
  const rest = route.sub
    .split("/")
    .filter((part) => part.length > 0)
    .map(encodeURIComponent)
    .join("/");
  return rest ? `#/${route.screen}/${rest}` : `#/${route.screen}`;
}

export function sameRoute(a: Route, b: Route): boolean {
  return a.screen === b.screen && a.sub === b.sub;
}
