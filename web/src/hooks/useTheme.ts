import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { THEME_CACHE_KEY, parseThemePref, resolveTheme } from "../lib/theme";
import type { ResolvedTheme, ThemePref } from "../lib/theme";
import { useMediaQuery } from "./useMediaQuery";

export type ThemeController = {
  pref: ThemePref;
  resolved: ResolvedTheme;
  /** Applies a preference locally and refreshes the pre-load cache. */
  setPref: (pref: ThemePref) => void;
};

function readCache(): ThemePref {
  try {
    return parseThemePref(window.localStorage.getItem(THEME_CACHE_KEY)) ?? "system";
  } catch {
    return "system";
  }
}

/** Owns the theme: sets `data-theme` on <html> and follows the system live. */
export function useThemeController(): ThemeController {
  const [pref, setPrefState] = useState<ThemePref>(readCache);
  const systemDark = useMediaQuery("(prefers-color-scheme: dark)");
  const resolved = resolveTheme(pref, systemDark);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
  }, [resolved]);

  const setPref = useCallback((next: ThemePref) => {
    setPrefState(next);
    try {
      window.localStorage.setItem(THEME_CACHE_KEY, next);
    } catch {
      // The cache only prevents a flash on load; losing it is harmless.
    }
  }, []);

  return useMemo(() => ({ pref, resolved, setPref }), [pref, resolved, setPref]);
}

export const ThemeContext = createContext<ThemeController | null>(null);

export function useTheme(): ThemeController {
  const controller = useContext(ThemeContext);
  if (!controller) throw new Error("useTheme must be used inside ThemeContext");
  return controller;
}
