// The welcome steps that follow creating the owner: which step comes next,
// what the browser can tell us about time zone and week start, and checks.
//
// Progress lives in the `setup.welcome` setting on the server, so it follows
// the owner to any device. Its value names the last step that was finished.

export const WELCOME_SETTING_KEY = "setup.welcome";
export const NAME_SETTING_KEY = "owner.name";
export const TIME_ZONE_SETTING_KEY = "owner.timezone";
export const WEEK_START_SETTING_KEY = "owner.week_starts";

export type WelcomeProgress = "new" | "about_you" | "model" | "done";
export type WelcomeStep = "about" | "model" | "finish";

/** Creating the owner is step 1; the welcome steps are 2 to 4. */
export const TOTAL_STEPS = 4;
export const WELCOME_STEPS: readonly WelcomeStep[] = ["about", "model", "finish"];

/** "Step 2 of 4" for a welcome step. */
export function stepLabel(step: WelcomeStep): string {
  return `Step ${WELCOME_STEPS.indexOf(step) + 2} of ${TOTAL_STEPS}`;
}

export function parseProgress(value: unknown): WelcomeProgress {
  return value === "about_you" || value === "model" || value === "done" ? value : "new";
}

/**
 * Which welcome step to show, or null for the app itself.
 *
 * `chatReady` is null while it is not known (still loading, or the models
 * could not be read). An installation that never started the steps but can
 * already chat is treated as finished, so nobody who set things up before
 * the welcome steps existed is sent through them. When it is not known, the
 * app is shown rather than blocking the owner.
 */
export function welcomeStep(progress: WelcomeProgress, chatReady: boolean | null): WelcomeStep | null {
  if (progress === "done") return null;
  if (progress === "about_you") return "model";
  if (progress === "model") return "finish";
  return chatReady === false ? "about" : null;
}

/** True when an installation that never started the steps should simply be marked finished. */
export function adoptsExistingSetup(progress: WelcomeProgress, chatReady: boolean | null): boolean {
  return progress === "new" && chatReady === true;
}

/** The progress to save once a step is finished. */
export function progressAfter(step: WelcomeStep): WelcomeProgress {
  return step === "about" ? "about_you" : step === "model" ? "model" : "done";
}

export function previousStep(step: WelcomeStep): WelcomeStep | null {
  const index = WELCOME_STEPS.indexOf(step);
  return index > 0 ? (WELCOME_STEPS[index - 1] ?? null) : null;
}

// --- can chat answer? ------------------------------------------------------------

type RoleLike = { role: string; primary: { provider_id: string } | null };
type ProviderLike = { id: string; enabled: boolean; needs_key: boolean; has_key: boolean };

/**
 * Whether chat has a model it may use: the default role (which chat asks for)
 * points at a provider that exists, is switched on and has the key it needs.
 */
export function chatModelReady(roles: readonly RoleLike[], providers: readonly ProviderLike[]): boolean {
  const primary = roles.find((entry) => entry.role === "default")?.primary;
  if (!primary) return false;
  const provider = providers.find((item) => item.id === primary.provider_id);
  return provider !== undefined && provider.enabled && !(provider.needs_key && !provider.has_key);
}

// --- time zone -------------------------------------------------------------------

/** True for a time zone name this browser knows. Empty or spaced text never is. */
export function isKnownTimeZone(name: string): boolean {
  if (!name || name !== name.trim() || /\s/.test(name)) return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: name });
    return true;
  } catch {
    return false;
  }
}

/** The browser's own time zone, or null when it cannot say or names one it does not know. */
export function browserTimeZone(resolve: () => string | undefined = () => Intl.DateTimeFormat().resolvedOptions().timeZone): string | null {
  try {
    const name = resolve();
    return name && isKnownTimeZone(name) ? name : null;
  } catch {
    return null;
  }
}

/** Every time zone the browser lists, with `extra` (the saved or browser one) included and sorted in. */
export function timeZoneList(supported: readonly string[], extra: readonly (string | null)[] = []): string[] {
  const all = new Set(supported);
  for (const name of extra) if (name && isKnownTimeZone(name)) all.add(name);
  return [...all].sort((a, b) => a.localeCompare(b));
}

/** The browser's list of time zone names; empty when it has none. */
export function supportedTimeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
}

function normalise(text: string): string {
  return text.toLowerCase().replace(/[_/\s-]+/g, " ").trim();
}

/** Names matching typed text: every word must appear; spaces, underscores and slashes count the same. */
export function filterTimeZones(names: readonly string[], query: string): string[] {
  const words = normalise(query).split(" ").filter(Boolean);
  if (words.length === 0) return [...names];
  return names.filter((name) => {
    const text = normalise(name);
    return words.every((word) => text.includes(word));
  });
}

/** "America/New_York" → "America / New York", for showing. The stored name is unchanged. */
export function timeZoneLabel(name: string): string {
  return name.replace(/_/g, " ").replace(/\//g, " / ");
}

// --- week start ------------------------------------------------------------------

export type WeekStart = "monday" | "sunday" | "saturday";

/** Maps an ISO weekday (1 = Monday … 7 = Sunday) to a choice the setting offers. */
export function weekStartFromDay(day: unknown): WeekStart | null {
  if (day === 1) return "monday";
  if (day === 7) return "sunday";
  if (day === 6) return "saturday";
  return null;
}

type LocaleWithWeek = { getWeekInfo?: () => { firstDay?: unknown }; weekInfo?: { firstDay?: unknown } };

/**
 * The first day of the week for a locale, when the browser can say and the
 * setting offers that day. Older browsers expose `weekInfo`, newer ones
 * `getWeekInfo()`; others cannot tell, and the answer is null.
 */
export function browserWeekStart(locale: string | undefined, make: (tag: string) => unknown = (tag) => new Intl.Locale(tag)): WeekStart | null {
  if (!locale) return null;
  try {
    const info = make(locale) as LocaleWithWeek;
    const week = typeof info.getWeekInfo === "function" ? info.getWeekInfo() : info.weekInfo;
    return weekStartFromDay(week?.firstDay);
  } catch {
    return null;
  }
}

// --- the About you step ----------------------------------------------------------

export type AboutInput = { name: string; timeZone: string; weekStart: string };
export type AboutErrors = { name?: string; timeZone?: string; weekStart?: string };

export const MAX_NAME_LENGTH = 120;

export function validateAbout(input: AboutInput, weekChoices: readonly string[]): AboutErrors {
  const errors: AboutErrors = {};
  const name = input.name.trim();
  if (!name) errors.name = "Enter your name.";
  else if (name.length > MAX_NAME_LENGTH) errors.name = `Use at most ${MAX_NAME_LENGTH} characters.`;
  if (!isKnownTimeZone(input.timeZone)) errors.timeZone = "Choose a time zone from the list.";
  if (!weekChoices.includes(input.weekStart)) errors.weekStart = "Choose the day your week starts on.";
  return errors;
}

// --- the last step ---------------------------------------------------------------

/** The screens the last step points at. Apps is among them: it is where most of JimmyHQ's use lives. */
export const FINISH_SCREENS = ["chat", "tasks", "village", "apps"] as const;
