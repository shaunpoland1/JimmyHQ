// The installed apps as the shell needs them: which screens to list in the
// sidebar and what `#/app/<id>/<screen>` should show. Pure, so it is tested.

import { BADGE_MAX, isAppId } from "./bridge.ts";
import type { Tier } from "./bridge.ts";

/** One screen an app adds. `described` means JimmyHQ draws it itself; otherwise it is a page in a frame. */
export type AppScreen = { id: string; label: string; described: boolean };

/**
 * The pictures an app may choose for its sidebar line. The shell draws each one itself
 * (`appIcons.ts`); an app names one and never brings markup or an image. Keep in step with
 * `ICONS` in the server's manifest reader.
 */
export const APP_ICON_NAMES = ["book", "calendar", "camera", "chart", "heart", "home", "image", "mail", "megaphone", "mic", "newspaper", "package", "pen", "truck", "users", "wallet"] as const;
export type AppIconName = (typeof APP_ICON_NAMES)[number];

/** The icon an app named, or null when it named none or one this shell does not draw. */
export function appIconName(raw: unknown): AppIconName | null {
  return typeof raw === "string" && (APP_ICON_NAMES as readonly string[]).includes(raw) ? (raw as AppIconName) : null;
}

export type InstalledApp = {
  id: string;
  name: string;
  version: string;
  tier: Tier;
  enabled: boolean;
  /** The picture the app chose for its sidebar line, if it chose one the shell draws. */
  icon: AppIconName | null;
  screens: AppScreen[];
  /** How many things wait on the owner, as the app's own program last said (`ui.count`), or null when it has not said or is switched off. */
  count: number | null;
};

const SCREEN_ID = /^[a-z][a-z0-9_]{0,30}$/;

function text(value: unknown, max: number): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim().slice(0, max) : null;
}

function parseScreen(raw: unknown): AppScreen | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  const id = typeof row.id === "string" && SCREEN_ID.test(row.id) ? row.id : null;
  if (!id) return null;
  return { id, label: text(row.label, 60) ?? id, described: row.described === true };
}

function parseApp(raw: unknown): InstalledApp | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  if (!isAppId(row.id)) return null;
  const screens = Array.isArray(row.screens) ? row.screens.map(parseScreen).filter((screen) => screen !== null) : [];
  return {
    id: row.id,
    name: text(row.name, 60) ?? row.id,
    version: text(row.version, 40) ?? "",
    tier: row.tier === "full" ? "full" : "sandboxed",
    // Anything but a plain `true` is treated as switched off.
    enabled: row.enabled === true,
    icon: appIconName(row.icon),
    screens,
    count: serverCount(row.count),
  };
}

/** The program's number for the sidebar: a whole number from 0 up, held to what a page may say; anything else is no number. */
export function serverCount(raw: unknown): number | null {
  if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0) return null;
  return Math.min(BADGE_MAX, Math.floor(raw));
}

export const DEFAULT_REFRESH_MS = 60_000;
const MIN_REFRESH_MS = 10_000;
const MAX_REFRESH_MS = 3_600_000;

/** How often to read the list again: what the server's `refresh_s` setting says, held between 10 s and an hour, or a minute. */
export function refreshMs(raw: unknown): number {
  const seconds = raw && typeof raw === "object" ? (raw as { refresh_s?: unknown }).refresh_s : undefined;
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return DEFAULT_REFRESH_MS;
  return Math.min(MAX_REFRESH_MS, Math.max(MIN_REFRESH_MS, Math.round(seconds * 1000)));
}

/** What a page last said with `ui.badge`, and whether its frame is still open. */
export type PageBadge = { count: number; open: boolean };

/**
 * The number beside an app in the sidebar. While the app's page is open, its own number wins,
 * even a 0 that clears the program's. Once the page is closed the program's number takes over
 * when it has one; an app with none (a sandboxed app) keeps what its page last said.
 */
export function shownBadge(page: PageBadge | undefined, server: number | null): number | undefined {
  if (page?.open) return page.count;
  return server ?? page?.count;
}

/**
 * Reads the server's list of installed apps (`GET /api/apps`: `{apps: [...]}`),
 * which holds only what the shell draws: no permissions and no file paths.
 * A row that is not an app is dropped rather than shown broken.
 */
export function parseInstalled(raw: unknown): InstalledApp[] {
  const rows = Array.isArray(raw) ? raw : raw && typeof raw === "object" && Array.isArray((raw as { apps?: unknown }).apps) ? (raw as { apps: unknown[] }).apps : [];
  const seen = new Set<string>();
  const apps: InstalledApp[] = [];
  for (const row of rows) {
    const app = parseApp(row);
    if (!app || seen.has(app.id)) continue;
    seen.add(app.id);
    apps.push(app);
  }
  return apps;
}

/** The part of the address after `#/app/`: `<id>/<screen>`. */
export function appSub(appId: string, screenId: string): string {
  return `${appId}/${screenId}`;
}

export type AppNavItem = { key: string; sub: string; appId: string; label: string; title: string; icon: AppIconName | null };

/** One sidebar line per screen of each app that is switched on, in the server's order. */
export function navItems(apps: readonly InstalledApp[]): AppNavItem[] {
  return apps
    .filter((app) => app.enabled)
    .flatMap((app) =>
      app.screens.map((screen) => ({
        key: appSub(app.id, screen.id),
        sub: appSub(app.id, screen.id),
        appId: app.id,
        label: screen.label,
        title: screen.label === app.name ? app.name : `${app.name}: ${screen.label}`,
        icon: app.icon,
      })),
    );
}

export type AppTarget =
  | { state: "ok"; app: InstalledApp; screen: AppScreen }
  | { state: "missing"; appId: string | null }
  | { state: "disabled"; app: InstalledApp }
  | { state: "no_screen"; app: InstalledApp };

/**
 * What `#/app/<sub>` points at. With no screen named, an app's first screen
 * is shown. Every other case is a plain state: not installed, switched off,
 * or no such screen.
 */
export function findTarget(apps: readonly InstalledApp[], sub: string | null): AppTarget {
  const [appId, screenId, ...rest] = (sub ?? "").split("/");
  if (!appId || !isAppId(appId) || rest.length > 0) return { state: "missing", appId: null };
  const app = apps.find((item) => item.id === appId);
  if (!app) return { state: "missing", appId };
  if (!app.enabled) return { state: "disabled", app };
  const screen = screenId ? app.screens.find((item) => item.id === screenId) : app.screens[0];
  return screen ? { state: "ok", app, screen } : { state: "no_screen", app };
}

/** True for a feed event that changes which apps are installed or switched on. */
export function affectsInstalled(event: { kind: string }): boolean {
  return event.kind.startsWith("apps.");
}
