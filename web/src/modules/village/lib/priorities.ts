// Standing priorities: who each one applies to, and the checks on the form.

import type { AgentSummary, NewPriority, Priority, PriorityChange } from "../api.ts";
import { labelForId } from "./names.ts";

export const MAX_TITLE = 200;
export const MAX_WHY = 2000;
export const MAX_DETAILS = 8000;

type Who = Pick<AgentSummary, "id" | "name" | "role">;

/** "Everyone", or the names it applies to. An agent the roster no longer lists is shown by its id. */
export function appliesTo(priority: Pick<Priority, "agents">, agents: readonly Who[]): string {
  if (priority.agents.length === 0) return "Everyone";
  const names = priority.agents.map((id) => {
    return labelForId(id, agents);
  });
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export function tidyTitle(title: string): string {
  return title.split(/\s+/).filter(Boolean).join(" ");
}

export function titleProblem(title: string): string | null {
  const tidy = tidyTitle(title);
  if (!tidy) return "Give the priority a short title.";
  return tidy.length > MAX_TITLE ? `Keep the title to ${MAX_TITLE} characters or fewer.` : null;
}

/** Active ones first, each group in the order they were set. */
export function sortPriorities(priorities: readonly Priority[]): Priority[] {
  return [...priorities.filter((item) => item.active), ...priorities.filter((item) => !item.active)];
}

/** Only what differs from the stored priority, so an edit never overwrites a field it did not touch. */
export function priorityChange(before: Priority, after: NewPriority): PriorityChange {
  const change: PriorityChange = {};
  const title = tidyTitle(after.title);
  if (title !== before.title) change.title = title;
  if (after.why.trim() !== before.why) change.why = after.why.trim();
  if (after.details.trim() !== before.details) change.details = after.details.trim();
  if (after.active !== before.active) change.active = after.active;
  const same = after.agents.length === before.agents.length && after.agents.every((id) => before.agents.includes(id));
  if (!same) change.agents = after.agents;
  return change;
}

/** The priorities that reach one agent: those for everyone and those that name it. */
export function forAgent(priorities: readonly Priority[], agentId: string): Priority[] {
  return priorities.filter((item) => item.active && (item.agents.length === 0 || item.agents.includes(agentId)));
}
