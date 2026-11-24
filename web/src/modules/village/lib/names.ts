// What an agent is called on screen. One rule, used everywhere an agent is listed, so one
// agent never shows under two labels.

import { workingName } from "../../../lib/agentNames.ts";

export { workingName };

type Who = { id: string; name: string | null; role: string };

export type AgentLabel = {
  /** The main name: the name it chose, or the working name from its id. */
  title: string;
  /** Its role (its job), or null when it has none. */
  role: string | null;
  /** The quiet note beside the role, or null once it has a name. */
  note: string | null;
  /** True once the agent has picked a name itself. */
  named: boolean;
};

function sentenceCase(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

export const NOT_NAMED_NOTE = "has not chosen a name yet";

export function agentLabel(agent: Who): AgentLabel {
  const chosen = (agent.name ?? "").trim();
  const role = (agent.role ?? "").trim();
  const named = chosen !== "";
  return {
    title: named ? chosen : workingName(agent.id),
    role: role ? sentenceCase(role) : null,
    note: named ? null : NOT_NAMED_NOTE,
    named,
  };
}

/** The label to use when only an id is known (an agent the roster no longer lists). */
export function labelForId(id: string, everyone: readonly Who[]): string {
  const found = everyone.find((agent) => agent.id === id);
  return found ? agentLabel(found).title : workingName(id);
}
