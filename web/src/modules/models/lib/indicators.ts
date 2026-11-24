// Which providers the status bar shows, and what it says about each.

import type { HealthEntry } from "../api.ts";
import { isLocal } from "./providers.ts";

export type Indicator = { id: string; label: string; ok: boolean };

export const MAX_INDICATORS = 3;
export const DEFAULT_HEALTH_POLL_MS = 30_000;

/**
 * At most three indicators, local providers first. A provider that has never
 * been checked is left out: there is nothing true to say about it yet. When
 * exactly one local provider is shown it is called "Local model".
 */
export function pickIndicators(entries: readonly HealthEntry[], max: number = MAX_INDICATORS): Indicator[] {
  const checked = entries.filter((entry) => entry.ok !== null);
  const local = checked.filter((entry) => isLocal(entry));
  const hosted = checked.filter((entry) => !isLocal(entry));
  const soleLocal = local.length === 1;
  return [...local, ...hosted].slice(0, Math.max(0, max)).map((entry) => {
    const name = soleLocal && isLocal(entry) ? "Local model" : entry.label;
    return { id: `model:${entry.id}`, label: `${name} ${entry.ok ? "online" : "offline"}`, ok: entry.ok === true };
  });
}

/** How often to ask for health: what the server suggests, or 30 s. */
export function healthPollMs(intervalS: number | undefined): number {
  if (intervalS === undefined || !Number.isFinite(intervalS) || intervalS <= 0) return DEFAULT_HEALTH_POLL_MS;
  return Math.max(5_000, Math.round(intervalS * 1000));
}
