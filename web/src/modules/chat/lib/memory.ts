// Chat and memory: the words the composer's switches use, whether the model
// chosen may be sent memory, and what a reply says about the memory it was given.

import { formatHash } from "../../../lib/route.ts";
import type { ChatOptions, MemoryUsed, Target } from "../api.ts";
import { modelReady } from "./format.ts";

export const MEMORY_LABEL = "Use memory";
export const LOCAL_LABEL = "Local only";

/**
 * Whether a reply by the chosen target may be given memory, as the server
 * will decide it: always in a "local only" conversation or when the owner
 * lets hosted models see memory, otherwise only when a local model answers
 * first. null while the choices are still loading or the target is unknown,
 * and when no model is set up at all (nothing will answer, so there is no
 * hosted model to blame).
 */
export function memoryAllowed(options: ChatOptions | undefined, target: Target | null | undefined, localOnly: boolean): boolean | null {
  if (!options) return null;
  if (!modelReady(options)) return null;
  if (localOnly || options.memory.to_hosted) return true;
  if (!target) return null;
  if (target.kind === "role") return options.roles.find((item) => item.role === target.role)?.memory ?? null;
  return options.providers.find((item) => item.id === target.provider_id)?.memory ?? null;
}

/** The one line that says what "Use memory" means as it stands. */
export function memoryHelp(useMemory: boolean, allowed: boolean | null): string {
  if (!useMemory) return "Use memory is off: the model is shown nothing from your memory in this conversation.";
  if (allowed === false) {
    return "Use memory is on, but the model chosen here is hosted and memory is kept off hosted models, so replies are written without it. Choose a local model, or allow it under Settings, Chat.";
  }
  return "Use memory is on: before each reply, notes from your memory that bear on your message are shown to the model.";
}

export type LocalToggle = { disabled: boolean; reason: string | null };

/**
 * Whether the Local only switch has anything to do. When every provider that
 * is switched on runs on the owner's own machines, no hosted model can answer
 * and there is nothing to switch off. Left usable while the choices load.
 */
export function localToggle(options: Pick<ChatOptions, "providers"> | undefined): LocalToggle {
  if (!options || options.providers.length === 0) return { disabled: false, reason: null };
  if (options.providers.every(({ local }) => local)) {
    return { disabled: true, reason: "Every model you have set up already runs on your own machines, so there is nothing to switch off." };
  }
  return { disabled: false, reason: null };
}

/** The one line that says what "Local only" means as it stands. */
export function localHelp(localOnly: boolean): string {
  return localOnly
    ? "Local only is on: only models on your own machines may answer here."
    : "Local only is off: a hosted model may answer if the model chosen points at one.";
}

/**
 * The memory choice a new conversation is started with. Only a switch the
 * owner flipped themselves is sent; otherwise the server starts it as the
 * owner's setting says.
 */
export function newConversationMemory(chosen: boolean | null): { use_memory?: boolean } {
  return chosen === null ? {} : { use_memory: chosen };
}

export type MemoryNote =
  | { kind: "used"; label: string; detail: string | null }
  | { kind: "skipped"; reason: string };

/** What to show under a reply about memory, or null when memory was off for it. */
export function memoryNote(memory: MemoryUsed | null | undefined): MemoryNote | null {
  if (!memory) return null;
  const count = memory.passages.length;
  if (!memory.used || count === 0) return { kind: "skipped", reason: memory.reason ?? "Memory was on, and nothing from it was used." };
  const label = count === 1 ? "Used 1 memory" : `Used ${new Intl.NumberFormat().format(count)} memories`;
  const detail = memory.trimmed > 0 ? `${memory.trimmed === 1 ? "1 more was" : `${new Intl.NumberFormat().format(memory.trimmed)} more were`} found and left out for space.` : null;
  return { kind: "used", label, detail };
}

/** The link to a note on the Memory screen. */
export function noteHref(path: string): string {
  return formatHash({ screen: "memory", sub: path });
}

export type SwitchName = "local" | "record" | "memory" | "web";

/**
 * Which switch's explanation the composer shows: the one under the pointer,
 * else the one with the keyboard focus, else the one just changed. One line
 * at a time, never three.
 */
export function explained(hovered: SwitchName | null, focused: SwitchName | null, changed: SwitchName | null): SwitchName | null {
  return hovered ?? focused ?? changed;
}
