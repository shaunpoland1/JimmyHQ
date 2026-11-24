// Which Tape events mean part of the Village screen should be read again.

export type Touch = {
  /** The agent the event is about, when it is about one. */
  agent: string | null;
  roster: boolean;
  chat: boolean;
  priorities: boolean;
};

type Seen = { kind: string; payload: Record<string, unknown> };

/** What a live event changes here, or null when it changes nothing on this screen. */
export function touchFrom(event: Seen): Touch | null {
  if (!event.kind.startsWith("village.")) return null;
  const id = event.payload.agent_id;
  const agent = typeof id === "string" && id ? id : null;
  if (event.kind === "village.prioritized") return { agent: null, roster: false, chat: false, priorities: true };
  if (event.kind === "village.said") return { agent, roster: true, chat: true, priorities: false };
  return { agent, roster: true, chat: false, priorities: false };
}

/** True when a changed setting is one this screen reads. */
export function touchesVillageSettings(event: Seen): boolean {
  if (!event.kind.startsWith("setting")) return false;
  const key = event.payload.key;
  return typeof key === "string" && key.startsWith("village.");
}

type SettingLike = { key: string; value: unknown };

/** Vitals are shown unless a `village.show_vitals` setting exists and is off. */
export function showVitals(settings: readonly SettingLike[] | undefined): boolean {
  const setting = settings?.find((item) => item.key === "village.show_vitals");
  return setting ? setting.value !== false : true;
}

/** The most agents the village will hold, or null when the setting cannot be read. */
export function headcountCap(settings: readonly SettingLike[] | undefined): number | null {
  const value = settings?.find((item) => item.key === "village.headcount_cap")?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
