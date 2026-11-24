// Whether an agent has a model that can answer for it. Pure: the Village draws what this says.
//
// An agent made while no model was set up, or whose model role points at nothing, takes its
// turns and gets nowhere. Nothing used to say so; the owner saw an agent that never did anything.

import type { ModelChoice } from "../api.ts";

type RoleLike = { role: string; primary: { provider_id: string } | null };
type ProviderLike = { id: string; enabled: boolean; needs_key: boolean; has_key: boolean };

/** Where to set a model up, in the owner's words. */
export const WHERE_TO_SET_MODELS = "Add one under Settings, Models and roles.";

function providerUsable(id: string, providers: readonly ProviderLike[]): boolean {
  const found = providers.find((item) => item.id === id);
  return found !== undefined && found.enabled && !(found.needs_key && !found.has_key);
}

/** Whether a choice (a role, or one provider's model) leads to a provider that can answer. A role with no model of its own uses the default role's. */
export function choiceUsable(choice: ModelChoice | null | undefined, roles: readonly RoleLike[], providers: readonly ProviderLike[]): boolean {
  if (choice?.provider_id) return providerUsable(choice.provider_id, providers);
  const wanted = choice?.role || "default";
  const target = roles.find((entry) => entry.role === wanted)?.primary ?? roles.find((entry) => entry.role === "default")?.primary ?? null;
  return target !== null && providerUsable(target.provider_id, providers);
}

/**
 * What to say beside an agent whose models cannot answer, or null when they can. `config` is
 * the agent's own setup (null while it is not known, which says nothing).
 */
export function agentModelProblem(
  config: { beat: ModelChoice; night: ModelChoice } | null | undefined,
  roles: readonly RoleLike[] | undefined,
  providers: readonly ProviderLike[] | undefined,
): string | null {
  if (!config || !roles || !providers) return null;
  const beat = choiceUsable(config.beat, roles, providers);
  const night = choiceUsable(config.night, roles, providers);
  if (beat && night) return null;
  if (!beat) return `No model can answer for this agent, so it cannot think or work yet. ${WHERE_TO_SET_MODELS}`;
  return `Its night model has nothing set up behind it, so it cannot sort its day into memory. ${WHERE_TO_SET_MODELS}`;
}

/** What to say at the top of the Village when no model is set up at all, or null. */
export function villageModelProblem(roles: readonly RoleLike[] | undefined, providers: readonly ProviderLike[] | undefined): string | null {
  if (!roles || !providers) return null;
  return choiceUsable({ role: "default" }, roles, providers) ? null : `No model is set up, so none of your agents can think, work or answer yet. ${WHERE_TO_SET_MODELS}`;
}
