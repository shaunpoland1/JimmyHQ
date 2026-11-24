import { Monitor, Moon, Sun } from "lucide-react";
import { THEME_PREFS, themeLabel } from "../lib/theme";
import type { ThemePref } from "../lib/theme";

const ICONS = { light: Sun, system: Monitor, dark: Moon } as const;

type ThemeSwitchProps = { value: ThemePref; onChange: (pref: ThemePref) => void };

/** Three-way theme switch: light / system / dark. */
export function ThemeSwitch({ value, onChange }: ThemeSwitchProps) {
  return (
    <div role="radiogroup" aria-label="Theme" className="flex flex-none items-center rounded-lg border border-border bg-bg p-0.5">
      {THEME_PREFS.map((pref) => {
        const Icon = ICONS[pref];
        const selected = pref === value;
        return (
          <button
            key={pref}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={`${themeLabel(pref)} theme`}
            title={`${themeLabel(pref)} theme`}
            onClick={() => onChange(pref)}
            className={`flex h-6 w-7 items-center justify-center rounded-md transition-colors ${
              selected ? "bg-surface text-text shadow-sm" : "text-text-faint hover:text-text"
            }`}
          >
            <Icon size={14} aria-hidden="true" />
          </button>
        );
      })}
    </div>
  );
}
