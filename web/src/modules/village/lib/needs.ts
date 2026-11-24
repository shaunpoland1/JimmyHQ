// The five needs and mood, in words a person reads at a glance.
//
// A need is 0 to 100, but high is not always good. Energy, social and purpose are
// good when high; frustration and curiosity are urges that build, so for them a
// full bar is the one to worry about. Everything here keeps that polarity straight.

import { NEEDS } from "../api.ts";
import type { Mood, Need, Needs } from "../api.ts";

/** +1: high is good. -1: high is an unmet urge or a bad feeling. */
export const POLARITY: Record<Need, 1 | -1> = { energy: 1, frustration: -1, social: 1, curiosity: -1, purpose: 1 };

export const NEED_LABELS: Record<Need, string> = {
  energy: "Energy",
  frustration: "Frustration",
  social: "Company",
  curiosity: "Curiosity",
  purpose: "Purpose",
};

/** What each need is, said once, for a tooltip or a screen reader. */
export const NEED_HELP: Record<Need, string> = {
  energy: "How much it has left for the day. Low energy means rest or sleep soon.",
  frustration: "How stuck it feels. This one is better low: when it runs high the agent takes a break or sets the task aside.",
  social: "How much company it has had. Low means it will look for a chat with the others.",
  curiosity: "An urge that builds with routine. High means it wants to go and explore something.",
  purpose: "Whether its work has been landing. It rises only with work that was really finished.",
};

// Four words per need, for the quarters 0-24, 25-49, 50-74 and 75-100.
const WORDS: Record<Need, [string, string, string, string]> = {
  energy: ["running on empty", "tired", "steady", "rested"],
  frustration: ["calm", "a little tense", "frustrated", "very frustrated"],
  social: ["lonely", "could use company", "content", "well connected"],
  curiosity: ["settled", "interested", "restless", "itching to explore"],
  purpose: ["adrift", "unsure it matters", "useful", "driven"],
};

export type Tone = "good" | "fine" | "strained";

export type NeedReading = {
  need: Need;
  label: string;
  /** 0 to 100, clamped and rounded. */
  value: number;
  /** One or two plain words for where it stands. */
  word: string;
  /** How satisfied the need is, 0 to 1, whatever its polarity. */
  satisfaction: number;
  tone: Tone;
  /** True when a high value is the bad end. */
  highIsBad: boolean;
};

function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value));
}

/** 0 to 1: how well the need is met. For frustration and curiosity, low is met. */
export function satisfaction(need: Need, value: number): number {
  const v = clamp(value, 0, 100) / 100;
  return POLARITY[need] > 0 ? v : 1 - v;
}

export function toneOf(met: number): Tone {
  if (met >= 0.6) return "good";
  return met >= 0.35 ? "fine" : "strained";
}

export function readNeed(need: Need, raw: number): NeedReading {
  const value = Math.round(clamp(Number.isFinite(raw) ? raw : 0, 0, 100));
  const met = satisfaction(need, value);
  return {
    need,
    label: NEED_LABELS[need],
    value,
    word: WORDS[need][Math.min(3, Math.floor(value / 25))] ?? "",
    satisfaction: met,
    tone: toneOf(met),
    highIsBad: POLARITY[need] < 0,
  };
}

/** The needs the server sent, in their fixed order. A missing need is left out, never guessed. */
export function readNeeds(needs: Needs | undefined): NeedReading[] {
  if (!needs) return [];
  return NEEDS.filter((need) => typeof needs[need] === "number").map((need) => readNeed(need, needs[need] as number));
}

/** One sentence for a screen reader: "Energy 72 of 100, steady." with the polarity said when it matters. */
export function needSentence(reading: NeedReading): string {
  const direction = reading.highIsBad ? " Lower is better." : "";
  return `${reading.label} ${reading.value} of 100, ${reading.word}.${direction}`;
}

/** The need that most deserves a look, or null when all are comfortable. */
export function mostStrained(readings: readonly NeedReading[]): NeedReading | null {
  const worst = [...readings].sort((a, b) => a.satisfaction - b.satisfaction)[0];
  return worst && worst.tone === "strained" ? worst : null;
}

/**
 * Mood as the server computes it: valence (bad day to good day) and arousal (flat
 * to wired), each -1 to 1. Used only when the card carries the weights but no mood.
 */
export function computeMood(needs: Needs, weights: Partial<Record<Need, number>>): Mood | null {
  if (NEEDS.some((need) => typeof needs[need] !== "number" || typeof weights[need] !== "number")) return null;
  const total = NEEDS.reduce((sum, need) => sum + (weights[need] as number), 0) || 1;
  const valence = NEEDS.reduce((sum, need) => sum + ((weights[need] as number) / total) * (2 * satisfaction(need, needs[need] as number) - 1), 0);
  const centred = (need: Need) => ((needs[need] as number) - 50) / 50;
  const arousal = 0.5 * centred("energy") + 0.3 * centred("frustration") + 0.2 * centred("curiosity");
  return { valence: clamp(valence, -1, 1), arousal: clamp(arousal, -1, 1) };
}

const MOODS: Record<"good" | "even" | "bad", Record<"high" | "mid" | "low", string>> = {
  good: { high: "In good spirits and full of go", mid: "Content", low: "Calm and content" },
  even: { high: "Keyed up", mid: "On an even keel", low: "Quiet, a little flat" },
  bad: { high: "Tense and on edge", mid: "Having a hard day", low: "Worn down" },
};

/** Mood as one plain phrase, or null when it is not known. */
export function describeMood(mood: Mood | null | undefined): string | null {
  if (!mood || !Number.isFinite(mood.valence) || !Number.isFinite(mood.arousal)) return null;
  const day = mood.valence >= 0.3 ? "good" : mood.valence <= -0.15 ? "bad" : "even";
  const pace = mood.arousal >= 0.25 ? "high" : mood.arousal <= -0.25 ? "low" : "mid";
  return MOODS[day][pace];
}

export function moodTone(mood: Mood | null | undefined): Tone {
  if (!mood) return "fine";
  return mood.valence >= 0.3 ? "good" : mood.valence <= -0.15 ? "strained" : "fine";
}
