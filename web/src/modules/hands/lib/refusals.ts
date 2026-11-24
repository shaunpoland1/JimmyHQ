// What the server refused, remembered so the screen can say it next to the choice it was
// about, even in the next form. Nothing is hidden because of it: the owner may still pick
// the level and the server answers again.

import type { Effect, Level } from "../api.ts";
import { refusalIsAboutLevel } from "./wording.ts";

export type RefusalMemory = Map<string, string>;

const keyOf = (effect: Effect, level: Level) => `${effect}:${level}`;

/** Notes a refusal of a level. Returns true when it was about the level, false when it was about something else. */
export function noteRefusal(memory: RefusalMemory, effect: Effect, level: Level, sentence: string): boolean {
  if (!refusalIsAboutLevel(sentence)) return false;
  memory.set(keyOf(effect, level), sentence);
  return true;
}

/** The sentence the server gave the last time this level was refused for this kind of action, if any. */
export function remembered(memory: RefusalMemory, effect: Effect, level: Level): string | null {
  return memory.get(keyOf(effect, level)) ?? null;
}

/** Forgets what was said about a level once it is accepted. */
export function forgetRefusal(memory: RefusalMemory, effect: Effect, level: Level): void {
  memory.delete(keyOf(effect, level));
}
