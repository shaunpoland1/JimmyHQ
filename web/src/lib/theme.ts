// Theme preference handling. Framework-free so it can be unit tested.

export type ThemePref = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_PREFS: readonly ThemePref[] = ["light", "system", "dark"];

/** localStorage key used only as a pre-load cache (see public/theme-boot.js). */
export const THEME_CACHE_KEY = "jimmyhq.theme";

/** The server setting the theme switch reads and writes. */
export const THEME_SETTING_KEY = "appearance.theme";

export function parseThemePref(value: unknown): ThemePref | null {
  return value === "light" || value === "dark" || value === "system" ? value : null;
}

export function resolveTheme(pref: ThemePref, systemPrefersDark: boolean): ResolvedTheme {
  if (pref === "system") return systemPrefersDark ? "dark" : "light";
  return pref;
}

export function themeLabel(pref: ThemePref): string {
  return pref === "light" ? "Light" : pref === "dark" ? "Dark" : "System";
}
