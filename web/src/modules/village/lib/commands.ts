// Birth and retirement are asked for, then done by the village process. This is the
// wording for that wait, and the checks on what the create and setup forms send.

import type { AgentConfig, Capability, CommandState, ModelChoice, NewAgent } from "../api.ts";

/** How the model picker names a choice. Structurally the models module's `PickerTarget`. */
export type Target = { kind: "role"; role: string } | { kind: "model"; provider_id: string; model: string };

export const DEFAULT_BEAT: Target = { kind: "role", role: "default" };
export const DEFAULT_NIGHT: Target = { kind: "role", role: "smart" };

export function toChoice(target: Target): ModelChoice {
  return target.kind === "role" ? { role: target.role } : { provider_id: target.provider_id, model: target.model };
}

export function toTarget(choice: ModelChoice | null | undefined, fallback: Target): Target {
  if (choice?.provider_id && choice.model) return { kind: "model", provider_id: choice.provider_id, model: choice.model };
  return choice?.role ? { kind: "role", role: choice.role } : fallback;
}

export type RoleInfo = { role: string; model: string | null; provider_label: string | null; local: boolean | null };
export type ProviderInfo = { id: string; label: string; local: boolean };
export type ChoiceInfo = { label: string; detail: string; onMachine: boolean | null };

/** A model choice as words, from what the Models screen knows. */
export function describeChoice(choice: ModelChoice | null | undefined, fallback: Target, roles: readonly RoleInfo[], providers: readonly ProviderInfo[]): ChoiceInfo {
  const target = toTarget(choice, fallback);
  if (target.kind === "model") {
    const provider = providers.find((item) => item.id === target.provider_id);
    return { label: target.model, detail: provider?.label ?? "a provider that is no longer set up", onMachine: provider ? provider["local"] : null };
  }
  const role = roles.find((item) => item.role === target.role);
  if (!role?.model) return { label: `The ${target.role} role`, detail: "not set up yet", onMachine: null };
  return { label: `The ${target.role} role`, detail: role.provider_label ? `${role.model} on ${role.provider_label}` : role.model, onMachine: role["local"] };
}

/** A warning when "local only" is on but a chosen model is hosted, or null. */
export function localOnlyProblem(localOnly: boolean, beat: ChoiceInfo, night: ChoiceInfo): string | null {
  if (!localOnly) return null;
  const hosted = [beat.onMachine === false ? "everyday" : null, night.onMachine === false ? "night" : null].filter(Boolean);
  if (hosted.length === 0) return null;
  return `The ${hosted.join(" and ")} model is hosted. With "local only" on, this agent will not use it: pick a model that runs on your own machines, or switch "local only" off.`;
}

const MAX_SEED = 2 ** 31 - 1;
export const MAX_ROLE = 80;

export type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };

/** The optional seed: blank is "roll one for me". */
export function parseSeed(text: string): Parsed<number | null> {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, value: null };
  if (!/^\d+$/.test(trimmed) || Number(trimmed) > MAX_SEED) {
    return { ok: false, error: `A seed is a whole number from 0 to ${MAX_SEED}. Leave it blank to have one rolled.` };
  }
  return { ok: true, value: Number(trimmed) };
}

export function roleProblem(role: string): string | null {
  return role.trim().length > MAX_ROLE ? `Keep the role to ${MAX_ROLE} characters or fewer.` : null;
}

/** One switch in an agent's setup: a capability the server knows, or one the agent holds that it no longer does. */
export type CapabilityRow = {
  id: string;
  label: string;
  description: string;
  on: boolean;
  /** What is not set up yet, when the capability cannot work as things stand. */
  problem: string | null;
  /** The settings section that fixes it, when there is one. */
  settings: string | null;
  /** False for a name the agent holds that the server does not offer: it can only be switched off. */
  known: boolean;
};

export const UNKNOWN_CAPABILITY = "This JimmyHQ no longer offers it, so it does nothing. Switch it off to remove it.";

/** The switches to show: every capability on offer, then anything else the agent holds. */
export function capabilityRows(offered: readonly Capability[] | undefined, held: readonly string[]): CapabilityRow[] {
  const rows: CapabilityRow[] = (offered ?? []).map((item) => ({
    id: item.id,
    label: item.label || item.id,
    description: item.description ?? "",
    on: held.includes(item.id),
    problem: item.problem ?? null,
    settings: item.settings ?? null,
    known: true,
  }));
  const seen = new Set(rows.map((row) => row.id));
  for (const id of held) {
    if (seen.has(id)) continue;
    seen.add(id);
    rows.push({ id, label: id, description: UNKNOWN_CAPABILITY, on: true, problem: null, settings: null, known: false });
  }
  return rows;
}

/** The held list after one switch is flipped, in a steady order and without repeats. */
export function toggleCapability(held: readonly string[], id: string, on: boolean): string[] {
  const rest = held.filter((item) => item !== id);
  return [...new Set(on ? [...rest, id] : rest)];
}

/** What the read-only setup shows for a held capability: its label when the server knows it. */
export function capabilityLabel(offered: readonly Capability[] | undefined, id: string): string {
  return offered?.find((item) => item.id === id)?.label || id;
}

export function newAgent(role: string, seed: number | null, beat: Target, night: Target, localOnly: boolean): NewAgent {
  return { role: role.trim(), seed, models: { beat: toChoice(beat), night: toChoice(night) }, local_only: localOnly };
}

export function sameConfig(a: AgentConfig, b: AgentConfig): boolean {
  const key = (config: AgentConfig) =>
    JSON.stringify([config.role.trim(), toChoice(toTarget(config.beat, DEFAULT_BEAT)), toChoice(toTarget(config.night, DEFAULT_NIGHT)), config.local_only, [...config.capabilities].sort()]);
  return key(a) === key(b);
}

// The village process looks for commands every second, so a short wait is normal.
export const SLOW_AFTER_MS = 8_000;
export const POLL_MS = 1_000;

export const NOT_RUNNING =
  "The village process is not running, so nothing has happened yet. Start it with \"jimmyhq run\" (or \"jimmyhq village\" beside the web server). Your request is saved and will be carried out as soon as it starts.";

export type Progress = {
  phase: "waiting" | "slow" | "stalled" | "done" | "refused";
  text: string;
};

type Wait = {
  kind: "birth" | "retire" | "rollback";
  state: CommandState | undefined;
  elapsedMs: number;
  /** Whether the server reports the village process alive; null when it does not say. */
  processOk: boolean | null;
};

/** Where an asked-for birth or retirement stands, as one sentence. */
export function commandProgress({ kind, state, elapsedMs, processOk }: Wait): Progress {
  if (state?.done) {
    const result = state.result;
    if (result && result.ok === false) {
      return { phase: "refused", text: result.error?.trim() || "The village refused, and gave no reason. Nothing was changed. Try again." };
    }
    if (kind === "retire") return { phase: "done", text: "Retired. Its folder is archived and what it shared is still readable." };
    if (kind === "rollback") {
      const undo = typeof result?.undo === "number" ? ` Its memory as it was just before is kept as snapshot ${result.undo}, so this can be undone by rolling back to that one.` : "";
      const put = result?.paths && result.paths.length > 0 ? ` Restored: ${result.paths.join(", ")}.` : "";
      return { phase: "done", text: `Rolled back${typeof result?.to === "number" ? ` to snapshot ${result.to}` : ""}.${put}${undo}` };
    }
    const tasks = result?.tasks ?? 0;
    const starter = tasks > 0 ? ` It has ${tasks} starter ${tasks === 1 ? "task" : "tasks"} to find its feet.` : "";
    return { phase: "done", text: `Born${result?.agent_id ? ` as ${result.agent_id}` : ""}. It will take its first turn shortly.${starter}` };
  }
  if (processOk === false) return { phase: "stalled", text: NOT_RUNNING };
  if (elapsedMs >= SLOW_AFTER_MS) {
    return {
      phase: "slow",
      text: "This is taking longer than usual. The village process may be busy or stopped; the request is saved and will be carried out when it is picked up.",
    };
  }
  const waiting = kind === "birth" ? "Waiting for the village to welcome it…" : kind === "rollback" ? "Waiting for the village to pause it between turns and put its memory back…" : "Waiting for the village to see it out…";
  return { phase: "waiting", text: waiting };
}
