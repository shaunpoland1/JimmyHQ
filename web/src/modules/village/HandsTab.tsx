import { AgentIntegrations } from "../hands/AgentIntegrations";
import type { AgentCard } from "./api";
import { displayName } from "./lib/status";

/** The agent's Integrations tab. The screens live in the hands module; this is only the tab. */
export function HandsTab({ agent }: { agent: AgentCard }) {
  return <AgentIntegrations agent={agent.id} agentName={displayName(agent)} retired={agent.retired} />;
}
