import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import type { Setting } from "../api";
import { Field } from "../components/Field";
import { ThemeSwitch } from "../components/ThemeSwitch";
import { useSettingActions } from "../hooks/useSettings";
import { useTheme } from "../hooks/useTheme";
import { THEME_SETTING_KEY, parseThemePref } from "../lib/theme";
import type { ThemePref } from "../lib/theme";
import {
  NAME_SETTING_KEY,
  TIME_ZONE_SETTING_KEY,
  WEEK_START_SETTING_KEY,
  browserTimeZone,
  browserWeekStart,
  isKnownTimeZone,
  validateAbout,
} from "../lib/welcome";
import type { AboutErrors } from "../lib/welcome";
import { WelcomeTimeZoneField } from "./WelcomeTimeZoneField";

type WelcomeAboutProps = {
  settings: readonly Setting[];
  busy: boolean;
  failure: string | null;
  onSave: (values: [string, string][]) => void;
};

const WEEK_FALLBACK = [
  { value: "monday", label: "Monday" },
  { value: "sunday", label: "Sunday" },
  { value: "saturday", label: "Saturday" },
];

function text(settings: readonly Setting[], key: string): string {
  const value = settings.find((item) => item.key === key)?.value;
  return typeof value === "string" ? value : "";
}

/** Step 2: the owner's name, time zone, first day of the week, and (one click) the theme. */
export function WelcomeAbout({ settings, busy, failure, onSave }: WelcomeAboutProps) {
  const weekId = useId();
  const theme = useTheme();
  const { save } = useSettingActions();
  const nameSetting = settings.find((item) => item.key === NAME_SETTING_KEY);
  const weekSetting = settings.find((item) => item.key === WEEK_START_SETTING_KEY);
  const themeSetting = settings.find((item) => item.key === THEME_SETTING_KEY);
  const weekChoices = (weekSetting?.choices ?? WEEK_FALLBACK).map((choice) => ({ value: String(choice.value), label: choice.label }));

  // A saved value wins; otherwise what this browser says.
  const [name, setName] = useState(() => text(settings, NAME_SETTING_KEY));
  const [timeZone, setTimeZone] = useState(() => {
    const saved = text(settings, TIME_ZONE_SETTING_KEY);
    return isKnownTimeZone(saved) ? saved : (browserTimeZone() ?? "");
  });
  const [weekStart, setWeekStart] = useState(() => {
    const fromBrowser = browserWeekStart(typeof navigator === "undefined" ? undefined : navigator.language);
    const saved = text(settings, WEEK_START_SETTING_KEY);
    const changed = weekSetting !== undefined && weekSetting.value !== weekSetting.default;
    return changed ? saved : (fromBrowser ?? saved ?? "monday");
  });
  const [errors, setErrors] = useState<AboutErrors>({});

  // Show the theme saved on the server, as the app itself would.
  const serverTheme = parseThemePref(themeSetting?.value);
  const { setPref } = theme;
  useEffect(() => {
    if (serverTheme) setPref(serverTheme);
  }, [serverTheme, setPref]);

  const changeTheme = (pref: ThemePref) => {
    setPref(pref);
    if (themeSetting) void save(THEME_SETTING_KEY, pref);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const found = validateAbout({ name, timeZone, weekStart }, weekChoices.map((choice) => choice.value));
    if (!nameSetting) delete found.name;
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const values: [string, string][] = [];
    if (nameSetting) values.push([NAME_SETTING_KEY, name.trim()]);
    values.push([TIME_ZONE_SETTING_KEY, timeZone], [WEEK_START_SETTING_KEY, weekStart]);
    onSave(values);
  };

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-5">
      {nameSetting ? (
        <Field
          label="Your name"
          name="name"
          autoComplete="name"
          value={name}
          maxLength={120}
          onChange={(event) => setName(event.target.value)}
          hint="How JimmyHQ and your agents refer to you."
          error={errors.name}
        />
      ) : null}

      <WelcomeTimeZoneField value={timeZone} onChange={setTimeZone} error={errors.timeZone} />

      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-xs font-medium text-text">Your week starts on</legend>
        <div role="radiogroup" aria-describedby={`${weekId}-hint`} className="flex flex-wrap gap-2">
          {weekChoices.map((choice) => (
            <label key={choice.value} className={`btn cursor-pointer has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-accent ${weekStart === choice.value ? "!border-accent !text-text" : "text-text-muted"}`}>
              <input type="radio" name="week-start" className="sr-only" value={choice.value} checked={weekStart === choice.value} onChange={() => setWeekStart(choice.value)} />
              {choice.label}
            </label>
          ))}
        </div>
        <p id={`${weekId}-hint`} className="text-xs text-text-faint">
          Used wherever JimmyHQ says "this week" or "next week".
        </p>
        {errors.weekStart ? (
          <p className="text-xs text-danger" role="alert">
            {errors.weekStart}
          </p>
        ) : null}
      </fieldset>

      {themeSetting ? (
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs font-medium text-text">Light or dark</span>
          <ThemeSwitch value={theme.pref} onChange={changeTheme} />
        </div>
      ) : null}

      {failure ? (
        <p role="alert" className="text-xs text-danger">
          {failure}
        </p>
      ) : null}
      <button type="submit" className="btn btn-primary" disabled={busy}>
        {busy ? "Saving…" : "Save and continue"}
      </button>
    </form>
  );
}
