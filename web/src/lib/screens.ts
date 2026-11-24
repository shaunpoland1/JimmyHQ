// The list of screens the shell can show.

export type ScreenId = "bench" | "tasks" | "chat" | "village" | "memory" | "apps" | "settings" | "app";

export type ScreenMeta = { id: ScreenId; label: string; description: string };

/** Screens in sidebar order. Settings is pinned separately at the bottom. */
export const NAV_SCREENS: readonly ScreenMeta[] = [
  { id: "bench", label: "Bench", description: "Review and approve what your agents propose before anything happens." },
  { id: "tasks", label: "Tasks", description: "Capture, organise and work through everything on your plate." },
  { id: "chat", label: "Chat", description: "Talk with your assistant and keep every conversation in one place." },
  { id: "village", label: "Village", description: "Meet your agents, see what each one is doing and adjust how they work." },
  { id: "memory", label: "Memory", description: "Browse and correct what JimmyHQ remembers for you." },
  { id: "apps", label: "Apps", description: "Install and manage apps that extend JimmyHQ." },
];

export const SETTINGS_SCREEN: ScreenMeta = {
  id: "settings",
  label: "Settings",
  description: "Change how JimmyHQ looks and behaves.",
};

/**
 * One screen of an installed app, at `#/app/<id>/<screen>`. It is not in the
 * sidebar's own list: the apps module supplies those lines (`useInstalledApps`).
 */
export const APP_SCREEN: ScreenMeta = {
  id: "app",
  label: "App",
  description: "A screen added by an app you installed.",
};

export const ALL_SCREENS: readonly ScreenMeta[] = [...NAV_SCREENS, SETTINGS_SCREEN, APP_SCREEN];

export const DEFAULT_SCREEN: ScreenId = "bench";

export function isScreenId(value: string): value is ScreenId {
  return ALL_SCREENS.some((screen) => screen.id === value);
}

export function screenMeta(id: ScreenId): ScreenMeta {
  return ALL_SCREENS.find((screen) => screen.id === id) ?? SETTINGS_SCREEN;
}
