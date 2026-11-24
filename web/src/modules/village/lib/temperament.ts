// Temperament in words. It is rolled once at birth and never edited, so this only reads.

import { NEEDS } from "../api.ts";
import type { LastNight, Temperament } from "../api.ts";
import { NEED_LABELS } from "./needs.ts";

type Trait = { key: keyof Temperament; low: number; high: number; words: [string, string, string] };

// Each trait's range at birth, and what its low, middle and high thirds mean.
const TRAITS: Trait[] = [
  { key: "energy_drain_rate", low: 0.6, high: 1.4, words: ["Keeps its energy a long time", "Tires at an ordinary pace", "Tires quickly"] },
  { key: "boredom_rate", low: 0.6, high: 1.4, words: ["Patient with routine", "Gets bored about as fast as most", "Bores easily and goes looking for something new"] },
  { key: "social_capacity", low: 0.5, high: 1.5, words: ["Happy with little company", "Likes some company", "Needs a lot of company"] },
  { key: "frustration_tolerance", low: 0.5, high: 1.5, words: ["Frustrated easily, so it steps away sooner", "Takes setbacks about as well as most", "Hard to rattle"] },
  { key: "risk_appetite", low: 0, high: 1, words: ["Cautious", "Neither bold nor cautious", "Bold"] },
];

function third(value: number, low: number, high: number): 0 | 1 | 2 {
  const at = (value - low) / (high - low);
  return at < 1 / 3 ? 0 : at < 2 / 3 ? 1 : 2;
}

function list(items: readonly string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** One short sentence per known trait. A trait the server did not send is left out. */
export function describeTemperament(temperament: Temperament | undefined): string[] {
  if (!temperament) return [];
  const out: string[] = [];
  for (const trait of TRAITS) {
    const value = temperament[trait.key];
    if (typeof value === "number" && Number.isFinite(value)) out.push(`${trait.words[third(value, trait.low, trait.high)]}.`);
  }
  const weights = temperament.mood_weights;
  if (weights) {
    const top = NEEDS.filter((need) => typeof weights[need] === "number").sort((a, b) => (weights[b] as number) - (weights[a] as number))[0];
    if (top) out.push(`Its mood leans most on ${NEED_LABELS[top].toLowerCase()}.`);
  }
  const offset = temperament.sleep_offset_minutes;
  if (typeof offset === "number" && Number.isFinite(offset)) {
    const minutes = Math.abs(Math.round(offset));
    if (minutes < 10) out.push("Keeps ordinary hours.");
    else out.push(offset < 0 ? `An early riser: its day runs ${minutes} minutes ahead.` : `A night owl: its day runs ${minutes} minutes behind.`);
  }
  const interests = (temperament.seed_curiosities ?? []).filter(Boolean);
  if (interests.length) out.push(`Started out curious about ${list(interests)}.`);
  return out;
}

/** What last night's sleep did, as sentences. */
export function describeNight(night: LastNight | null | undefined): { summary: string; by: string; rejected: string | null } | null {
  if (!night) return null;
  const ops = night.ops ?? 0;
  const summary = ops === 0 ? "It made no changes to its memory." : `It made ${ops} ${ops === 1 ? "change" : "changes"} to its memory.`;
  const by =
    night.by === "smart"
      ? "Sorted by its night model."
      : night.by === "fallback"
        ? "Sorted by its everyday model, because the night model kept failing. Check the night model in its setup."
        : `Sorted by ${night.by}.`;
  const refused = night.rejected ?? 0;
  const rejected = refused > 0 ? `${refused} proposed ${refused === 1 ? "change was" : "changes were"} rejected by the checks and left out.` : null;
  return { summary, by, rejected };
}
