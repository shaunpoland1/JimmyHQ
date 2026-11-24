// An app's roles, as the install dialog and the Roles panel show them: reading what the
// server sends, the owner's choice for each role, and the checks the server makes again
// (roles kept apart, two of them on one model). Pure: no React, no fetch.
//
//   GET /api/apps/_install/roles/<plan> -> { roles, agents }
//   GET /api/apps/<id>/roles            -> { roles (with holder), agents, warnings }
//   PUT /api/apps/<id>/roles            body { choices: { <role>: Choice } }

/**
 * The query key for a waiting install's roles. It sits outside the "apps" keys on purpose:
 * installing consumes the plan, so asking for its roles again (which refreshing every "apps"
 * query after an install would do) is answered 404.
 */
export function planRolesKey(plan: string): readonly [string, string] {
  return ["install-plan-roles", plan];
}

export type RoleTool = { name: string; description: string; effect: string };

export type RoleHolder = { agentId: string; label: string; created: boolean };

export type RoleInfo = {
  id: string;
  label: string;
  purpose: string;
  /** The charter's text, shown to the owner before it is given to an agent. Null if it could not be read. */
  charter: string | null;
  tools: RoleTool[];
  /** The model role suggested for an agent made for it. */
  model: string;
  /** The roles one agent must not hold together with this one (both ways). */
  notWith: string[];
  optional: boolean;
  holder: RoleHolder | null;
  /** The app's charter changed since it was given to the holder. */
  charterChanged: boolean;
  openWork: number;
};

export type AgentOption = { id: string; label: string; role: string; model: string };

export type RolesView = { roles: RoleInfo[]; agents: AgentOption[]; warnings: string[] };

/** The owner's choice for one role. `group` names one new agent; roles with the same group share it. */
export type Choice = { mode: "empty" } | { mode: "existing"; agentId: string } | { mode: "new"; group: string };

export type Choices = Record<string, Choice>;

const text = (value: unknown): string => (typeof value === "string" ? value : "");
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const record = (value: unknown): Record<string, unknown> => (value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {});

export function readTool(raw: unknown): RoleTool {
  const r = record(raw);
  return { name: text(r.name), description: text(r.description), effect: text(r.effect) };
}

export function readRole(raw: unknown): RoleInfo | null {
  const r = record(raw);
  if (!text(r.id)) return null;
  const holder = record(r.holder);
  return {
    id: text(r.id),
    label: text(r.label) || text(r.id),
    purpose: text(r.purpose),
    charter: typeof r.charter === "string" ? r.charter : null,
    tools: list(r.tools).map(readTool).filter((t) => t.name),
    model: text(r.model) || "default",
    notWith: list(r.not_with).filter((x): x is string => typeof x === "string"),
    optional: r.optional !== false,
    holder: text(holder.agent_id) ? { agentId: text(holder.agent_id), label: text(holder.label) || "An agent", created: holder.created === true } : null,
    charterChanged: r.charter_changed === true,
    openWork: typeof r.open_work === "number" ? r.open_work : 0,
  };
}

export function readAgent(raw: unknown): AgentOption | null {
  const r = record(raw);
  if (!text(r.id)) return null;
  return { id: text(r.id), label: text(r.label) || text(r.id), role: text(r.role), model: text(r.model) };
}

export function readRolesView(raw: unknown): RolesView {
  const r = record(raw);
  return {
    roles: list(r.roles).map(readRole).filter((x): x is RoleInfo => x !== null),
    agents: list(r.agents).map(readAgent).filter((x): x is AgentOption => x !== null),
    warnings: list(r.warnings).filter((x): x is string => typeof x === "string"),
  };
}

/** Nothing is forced: every role starts empty, and the app works its plain way. */
export function emptyChoices(roles: readonly RoleInfo[]): Choices {
  return Object.fromEntries(roles.map((role) => [role.id, { mode: "empty" } as Choice]));
}

/** The one-click "Create an agent for each role": each role its own new agent, so no pair kept apart is ever shared. */
export function newAgentForEach(roles: readonly RoleInfo[]): Choices {
  return Object.fromEntries(roles.map((role) => [role.id, { mode: "new", group: role.id } as Choice]));
}

/** The choices as the server takes them. Only the roles named are changed. */
export function choicesBody(choices: Choices): { choices: Record<string, Record<string, string>> } {
  const out: Record<string, Record<string, string>> = {};
  for (const [role, choice] of Object.entries(choices)) {
    if (choice.mode === "existing") out[role] = { mode: "existing", agent_id: choice.agentId };
    else if (choice.mode === "new") out[role] = { mode: "new", group: choice.group };
    else out[role] = { mode: "empty" };
  }
  return { choices: out };
}

/** Whether there is anything to send after installing: a role left empty needs no call. */
export function anythingChosen(choices: Choices): boolean {
  return Object.values(choices).some((choice) => choice.mode !== "empty");
}

/** Who would hold the role: an agent id, `new:<group>`, or null. */
export function whoHolds(choice: Choice | undefined): string | null {
  if (!choice || choice.mode === "empty") return null;
  return choice.mode === "existing" ? choice.agentId : `new:${choice.group}`;
}

/** Every pair of roles kept apart, once, as [a, b] with a before b. */
export function pairsApart(roles: readonly RoleInfo[]): [string, string][] {
  const seen = new Set<string>();
  const out: [string, string][] = [];
  for (const role of roles) {
    for (const other of role.notWith) {
      const pair = [role.id, other].sort() as [string, string];
      const key = pair.join(" ");
      if (!seen.has(key)) {
        seen.add(key);
        out.push(pair);
      }
    }
  }
  return out;
}

function labelOf(roles: readonly RoleInfo[], id: string): string {
  return roles.find((r) => r.id === id)?.label ?? id;
}

/** The sentence that stops the owner choosing one agent for two roles kept apart, or null. The server refuses it too. */
export function apartProblem(appName: string, roles: readonly RoleInfo[], choices: Choices): string | null {
  for (const [a, b] of pairsApart(roles)) {
    const x = whoHolds(choices[a]);
    if (x !== null && x === whoHolds(choices[b])) {
      return `One agent cannot be both ${labelOf(roles, a)} and ${labelOf(roles, b)}: ${appName} keeps them apart so that one checks the other. Choose someone else for one of them, or leave it empty.`;
    }
  }
  return null;
}

/** Two roles kept apart on the same model are allowed; the owner is told why it matters. */
export function sameModelWarnings(appName: string, roles: readonly RoleInfo[], agents: readonly AgentOption[], choices: Choices): string[] {
  const modelOf = (role: string): string | null => {
    const choice = choices[role];
    if (!choice || choice.mode === "empty") return null;
    if (choice.mode === "existing") return agents.find((a) => a.id === choice.agentId)?.model ?? null;
    const first = roles.find((r) => {
      const other = choices[r.id];
      return other?.mode === "new" && other.group === choice.group;
    });
    return `role:${first?.model ?? "default"}`;
  };
  const out: string[] = [];
  for (const [a, b] of pairsApart(roles)) {
    if (whoHolds(choices[a]) === whoHolds(choices[b])) continue;
    const ma = modelOf(a);
    if (ma !== null && ma === modelOf(b)) {
      out.push(`${labelOf(roles, a)} and ${labelOf(roles, b)} would run on the same model. ${appName} keeps them apart so that one can catch what the other misses, and one model tends to miss the same things twice. A different model for one of them is better.`);
    }
  }
  return out;
}

/** The value of a role's choice in its select: "empty", "new:<group>" or "agent:<id>". */
export function choiceValue(choice: Choice | undefined): string {
  if (!choice || choice.mode === "empty") return "empty";
  return choice.mode === "new" ? `new:${choice.group}` : `agent:${choice.agentId}`;
}

export function choiceFrom(value: string): Choice {
  if (value.startsWith("new:")) return { mode: "new", group: value.slice(4) || "new" };
  if (value.startsWith("agent:")) return { mode: "existing", agentId: value.slice(6) };
  return { mode: "empty" };
}

export type ChoiceOption = { value: string; label: string };

/** What a role's select offers: leave it empty, a new agent of its own, sharing a new agent another role gets, or an agent the owner has. */
export function choiceOptions(role: RoleInfo, roles: readonly RoleInfo[], agents: readonly AgentOption[], choices: Choices): ChoiceOption[] {
  const out: ChoiceOption[] = [
    { value: "empty", label: "Leave it empty (the app works without an agent)" },
    { value: `new:${role.id}`, label: `Create a new agent for ${role.label}` },
  ];
  const apart = new Set(role.notWith);
  for (const other of roles) {
    if (other.id === role.id || apart.has(other.id)) continue;
    const choice = choices[other.id];
    if (choice?.mode === "new" && choice.group === other.id) out.push({ value: `new:${other.id}`, label: `The same new agent as ${other.label}` });
  }
  for (const agent of agents) out.push({ value: `agent:${agent.id}`, label: agent.role ? `${agent.label} (${agent.role})` : agent.label });
  return out;
}

/** One line under a role on the app's page: who holds it now. */
export function holderLine(role: RoleInfo): string {
  if (!role.holder) return role.optional ? "Empty. The app does this part without an agent." : "Empty. The app asks for this role to be filled.";
  const work = role.openWork === 1 ? " 1 piece of work is with them." : role.openWork > 1 ? ` ${role.openWork} pieces of work are with them.` : "";
  return `Held by ${role.holder.label}.${work}`;
}

const LADDER = "waits for your say on the ladder";

/** What a tool's effect means for the owner, in a few words. */
export function effectWords(effect: string): string {
  if (effect === "read") return "only reads";
  if (effect === "record") return "writes into the app's own records only";
  const done: Record<string, string> = {
    send: "sends something out", create: "creates something", update: "changes something",
    delete: "deletes something", spend: "spends money", access: "opens access to something", change: "changes a setting",
  };
  return `${done[effect] ?? "changes something"}: ${LADDER}`;
}

/**
 * The tool's description cut to its first sentence, for one line the owner reads. The
 * whole description is written for the agent; its first sentence says what the tool does.
 */
export function toolSummary(description: string): string {
  const text = description.replace(/\s+/g, " ").trim();
  if (!text) return "";
  const end = /[.!?](?=\s|$)/.exec(text);
  const first = end ? text.slice(0, end.index + 1) : `${text}.`;
  return first.length > 160 ? `${first.slice(0, 157).trimEnd()}…` : first;
}

export type ToolLine = { summary: string; effect: string; name: string };

/** One tool as a role lists it: what it does, its effect in plain words, and its raw name as small print. */
export function toolLine(tool: RoleTool): ToolLine {
  return { summary: toolSummary(tool.description) || tool.name, effect: effectWords(tool.effect), name: tool.name };
}

/** What the toast says once the choices were sent. */
export function chosenToast(appName: string, result: unknown): string {
  const r = record(result);
  const filled = list(r.filled).length;
  const creating = list(r.creating).length;
  const parts: string[] = [];
  if (filled) parts.push(`${filled} ${filled === 1 ? "role was" : "roles were"} given`);
  if (creating) parts.push(`${creating} ${creating === 1 ? "agent is" : "agents are"} being created in the Village`);
  if (!parts.length) return `The roles of ${appName} were updated.`;
  return `${parts.join(", and ")} for ${appName}.`.replace(/^./, (c) => c.toUpperCase());
}
