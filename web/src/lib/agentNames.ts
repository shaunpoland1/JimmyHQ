// How an agent is written on screen, shared by every screen that draws one so a
// person can tell agents apart by letters, not only by colour.

/** "agent-a" is "Agent A", "agent-27" is "Agent 27". An id of another shape is shown as it is. */
export function workingName(id: string): string {
  const match = /^agent-([a-z0-9]+)$/i.exec((id ?? "").trim());
  if (!match) return (id ?? "").trim() || "New agent";
  const rest = match[1] ?? "";
  return `Agent ${/^\d+$/.test(rest) ? rest : rest.toUpperCase()}`;
}

/** One or two letters for an agent's badge: "Agent A" is "AA", "Wren" is "W". */
export function initialsOf(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? `${words[0]?.[0] ?? ""}${words[1]?.[0] ?? ""}` : (words[0] ?? "").slice(0, 1);
  return letters.toUpperCase() || "?";
}

/** The badge letters for an agent known only by its id ("agent-a" gives "AA"). */
export function initialsForId(id: string): string {
  return initialsOf(workingName(id));
}

// --- the name an agent chose ------------------------------------------------------------------
// An agent may pick its own name in the Village. Screens that know an agent only by its id (Tasks,
// the Bench) use the same rule as the Village's roster: the chosen name when there is one, else
// the working name made from the id. The chosen names come from the Village's roster, read once
// for every screen (`useAgentNames`); nothing here asks the server.

/** The names agents chose, by agent id. An agent that has not chosen one is not in it. */
export type ChosenNames = ReadonlyMap<string, string>;

export const NO_NAMES: ChosenNames = new Map();

const kept = new WeakMap<object, ChosenNames>();

/**
 * The chosen names in a roster (`GET /api/village/agents`: `[{id, name, ...}]`). A row without an
 * id, or whose name is empty or not text, has none; anything that is not a list gives none. The
 * same list read twice gives the same map, so a screen with many rows does the work once.
 */
export function chosenNames(roster: unknown): ChosenNames {
  if (!Array.isArray(roster)) return NO_NAMES;
  const seen = kept.get(roster);
  if (seen) return seen;
  const names = new Map<string, string>();
  for (const row of roster) {
    if (!row || typeof row !== "object") continue;
    const { id, name } = row as { id?: unknown; name?: unknown };
    if (typeof id !== "string" || typeof name !== "string") continue;
    const chosen = name.trim();
    if (id && chosen) names.set(id, chosen);
  }
  kept.set(roster, names);
  return names;
}

/** What an agent is called on screen: the name it chose, else its working name ("Agent A"). */
export function shownName(id: string, chosen: ChosenNames = NO_NAMES): string {
  return chosen.get((id ?? "").trim()) ?? workingName(id);
}

/** The badge letters for an agent: from the name it chose, else from its working name. */
export function shownInitials(id: string, chosen: ChosenNames = NO_NAMES): string {
  return initialsOf(shownName(id, chosen));
}

/** The Village's roster (living and retired) and its cache key: the Village screen's own, so one answer serves every screen. */
export const ROSTER_KEY = ["village", "agents"] as const;
export const ROSTER_PATH = "/api/village/agents?retired=true";
