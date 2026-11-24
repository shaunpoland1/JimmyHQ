// Roles: what each is for, editing their targets, and the warnings shown beside them.

import type { Effort, Provider, RoleBody, RoleEntry, Target, TargetInput } from "../api.ts";
import { isLocal } from "./providers.ts";

export const MAX_FALLBACKS = 8;
export const MAX_MODEL_LENGTH = 200;

const ROLE_USES: Record<string, string> = {
  default: "everyday chat and agents",
  cheap: "triage, tagging and summaries",
  smart: "planning and hard reasoning",
  code: "writing and reviewing code",
  vision: "screenshots, photos and PDFs",
  embeddings: "memory search",
};

/** One line saying what a role is used for; empty for a role this screen does not know. */
export function roleUse(role: string): string {
  return ROLE_USES[role] ?? "";
}

/** "the default role", "the default and code roles", "the a, b and c roles". */
export function roleList(roles: readonly string[]): string {
  if (roles.length === 0) return "no role";
  if (roles.length === 1) return `the ${roles[0]} role`;
  return `the ${roles.slice(0, -1).join(", ")} and ${roles[roles.length - 1]} roles`;
}

/** Names of the roles whose first choice or fallbacks point at a provider. */
export function rolesUsing(roles: readonly RoleEntry[], providerId: string): string[] {
  return roles
    .filter((entry) => [entry.primary, ...entry.fallbacks].some((target) => target?.provider_id === providerId))
    .map((entry) => entry.role);
}

export type TargetView = {
  providerLabel: string;
  model: string;
  effort: Effort | null;
  /** null when the provider no longer exists. */
  isLocal: boolean | null;
  state: "ok" | "off" | "missing";
};

export function viewTarget(target: Target, providers: readonly Provider[]): TargetView {
  const provider = providers.find((item) => item.id === target.provider_id);
  if (!provider) return { providerLabel: target.provider_id, model: target.model, effort: target.effort, isLocal: null, state: "missing" };
  return {
    providerLabel: provider.label,
    model: target.model,
    effort: target.effort,
    isLocal: isLocal(provider),
    state: provider.enabled ? "ok" : "off",
  };
}

/** Problems worth showing beside a role. Empty when all is well or the role is unset. */
export function roleWarnings(entry: Pick<RoleEntry, "primary" | "fallbacks">, providers: readonly Provider[]): string[] {
  if (!entry.primary) return [];
  const warnings: string[] = [];
  const primary = viewTarget(entry.primary, providers);
  if (primary.state === "missing") {
    warnings.push(`The first choice points at a provider that no longer exists (${entry.primary.provider_id}). Choose another.`);
  } else if (primary.state === "off") {
    warnings.push(`${primary.providerLabel} is switched off, so the first choice is skipped.`);
  }
  const fallbacks = entry.fallbacks.map((target) => ({ target, view: viewTarget(target, providers) }));
  const missing = fallbacks.filter((item) => item.view.state === "missing");
  if (missing.length) {
    const ids = [...new Set(missing.map((item) => item.target.provider_id))];
    warnings.push(`A fallback points at a provider that no longer exists (${ids.join(", ")}).`);
  }
  const off = [...new Set(fallbacks.filter((item) => item.view.state === "off").map((item) => item.view.providerLabel))];
  if (off.length) warnings.push(`${off.join(", ")} ${off.length === 1 ? "is" : "are"} switched off, so that fallback is skipped.`);
  const localFallback = fallbacks.some((item) => item.view.state === "ok" && item.view.isLocal === true);
  if (primary.isLocal === false && !localFallback) {
    warnings.push("The first choice is hosted and there is no local fallback: work for this role leaves your machines, and local-only requests cannot be answered.");
  }
  return warnings;
}

// --- editing --------------------------------------------------------------------

export type TargetDraft = { provider_id: string; model: string; effort: Effort | "" };

export function emptyTarget(providerId = ""): TargetDraft {
  return { provider_id: providerId, model: "", effort: "" };
}

export function toDraft(target: Target): TargetDraft {
  return { provider_id: target.provider_id, model: target.model, effort: target.effort ?? "" };
}

export function toInput(draft: TargetDraft): TargetInput {
  return { provider_id: draft.provider_id, model: draft.model.trim(), effort: draft.effort || null };
}

export function targetError(draft: TargetDraft): string | null {
  if (!draft.provider_id) return "Choose a provider.";
  const model = draft.model.trim();
  if (!model) return "Choose or type a model.";
  if (model.length > MAX_MODEL_LENGTH) return `A model name cannot be longer than ${MAX_MODEL_LENGTH} characters.`;
  if (/[\u0000-\u001f\u007f]/.test(model)) return "The model name contains characters that cannot be saved.";
  return null;
}

export type RoleDraft = { primary: TargetDraft | null; fallbacks: TargetDraft[] };

export function draftRole(entry: Pick<RoleEntry, "primary" | "fallbacks">): RoleDraft {
  return { primary: entry.primary ? toDraft(entry.primary) : null, fallbacks: entry.fallbacks.map(toDraft) };
}

export type RoleCheck = { ok: true; body: RoleBody } | { ok: false; primary: string | null; fallbacks: (string | null)[]; summary: string };

/** Checks a role draft and builds the body to save. */
export function checkRole(draft: RoleDraft): RoleCheck {
  if (!draft.primary) {
    if (draft.fallbacks.length === 0) return { ok: true, body: { primary: null, fallbacks: [] } };
    return {
      ok: false,
      primary: "Choose a first choice, or remove the fallbacks to clear the role.",
      fallbacks: draft.fallbacks.map(() => null),
      summary: "A role with fallbacks needs a first choice.",
    };
  }
  const primary = targetError(draft.primary);
  const fallbacks = draft.fallbacks.map(targetError);
  if (draft.fallbacks.length > MAX_FALLBACKS) {
    return { ok: false, primary, fallbacks, summary: `A role can have at most ${MAX_FALLBACKS} fallbacks.` };
  }
  if (primary || fallbacks.some(Boolean)) {
    return { ok: false, primary, fallbacks, summary: "Finish choosing a provider and a model for each line." };
  }
  return { ok: true, body: { primary: toInput(draft.primary), fallbacks: draft.fallbacks.map(toInput) } };
}

/** Moves one item up (-1) or down (+1); out-of-range moves return the list unchanged. */
export function moveItem<T>(items: readonly T[], index: number, delta: -1 | 1): T[] {
  const next = [...items];
  const to = index + delta;
  if (index < 0 || index >= next.length || to < 0 || to >= next.length) return next;
  const [item] = next.splice(index, 1);
  next.splice(to, 0, item as T);
  return next;
}

export function removeAt<T>(items: readonly T[], index: number): T[] {
  return items.filter((_, position) => position !== index);
}

export function replaceAt<T>(items: readonly T[], index: number, value: T): T[] {
  return items.map((item, position) => (position === index ? value : item));
}
