// What an agent is doing right now, in plain words, and how the roster is ordered,
// filtered and coloured.

import type { AgentCard, AgentSummary } from "../api.ts";
import { agentLabel } from "./names.ts";
import { initialsOf } from "../../../lib/agentNames.ts";

export type Doing = {
  /** "Working on the weekly report", "Asleep for the night". */
  headline: string;
  /** A second, quieter line, or null. */
  detail: string | null;
  state: "new" | "awake" | "resting" | "asleep" | "retired";
  /** When it is expected to wake or stop resting, unix milliseconds, if known. */
  untilMs: number | null;
};

type Seen = AgentSummary & Pick<AgentCard, "vitals">;

/**
 * A time from the village process as unix milliseconds. Its clock counts seconds,
 * the rest of the app milliseconds, so anything too small to be milliseconds is seconds.
 */
export function toMs(value: number | null | undefined): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return value < 1e11 ? Math.round(value * 1000) : Math.round(value);
}

const AWAKE: Record<string, string> = {
  explore: "Exploring something it is curious about",
  outlet: "Taking a break to cool off",
  social: "In the break room with the others",
};

export function describeDoing(agent: Seen): Doing {
  if (agent.retired) return { headline: "Retired", detail: "Its work is still here to read.", state: "retired", untilMs: null };
  const started = Boolean(agent.mode) || Boolean(agent.asleep) || Object.keys(agent.needs ?? {}).length > 0;
  if (!started) {
    return { headline: "Not started yet", detail: "It takes its first turn when the village process next runs.", state: "new", untilMs: null };
  }
  const vitals = agent.vitals ?? {};
  if (agent.asleep) {
    const untilMs = toMs(vitals.wake_at) ?? toMs(vitals.nap_until);
    if (vitals.sleep_kind === "nap") return { headline: "Taking a nap", detail: "One short sleep on a tired afternoon.", state: "asleep", untilMs };
    if (vitals.sleep_kind === "forced") {
      return { headline: "Asleep: it ran out of energy", detail: "It will wake once it has enough to go on.", state: "asleep", untilMs };
    }
    const night = vitals.sleep_kind === "night";
    return {
      headline: night ? "Asleep for the night" : "Asleep",
      detail: night ? "It sorts the day into memory while it sleeps." : null,
      state: "asleep",
      untilMs,
    };
  }
  if (agent.mode === "rest") {
    return { headline: "Resting", detail: "It stopped to get its energy back.", state: "resting", untilMs: toMs(vitals.rest_until) };
  }
  if (agent.mode === "work" || !agent.mode) {
    const task = (agent.current_task ?? "").trim();
    return {
      headline: task ? `Working on ${task}` : "Awake, between tasks",
      detail: task ? null : "It has no task in hand right now.",
      state: "awake",
      untilMs: null,
    };
  }
  return { headline: AWAKE[agent.mode] ?? agent.status_line ?? "Awake", detail: null, state: "awake", untilMs: null };
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** "in 20 minutes", "in 1 hour 10 minutes", "any moment now". */
export function untilText(untilMs: number, nowMs: number): string {
  const minutes = Math.round((untilMs - nowMs) / 60_000);
  if (minutes <= 0) return "any moment now";
  if (minutes < 60) return `in ${plural(minutes, "minute", "minutes")}`;
  if (minutes < 180) {
    const rest = minutes % 60;
    const hours = plural(Math.floor(minutes / 60), "hour", "hours");
    return rest === 0 ? `in ${hours}` : `in ${hours} ${plural(rest, "minute", "minutes")}`;
  }
  if (minutes < 36 * 60) return `in about ${plural(Math.round(minutes / 60), "hour", "hours")}`;
  return `in about ${plural(Math.round(minutes / 1440), "day", "days")}`;
}

/** What the agent needs from the owner, as short phrases; empty when nothing. */
export function waitingOnYou(agent: Pick<AgentSummary, "waiting" | "unanswered" | "retired">): string[] {
  if (agent.retired) return [];
  const out: string[] = [];
  const unanswered = agent.unanswered ?? 0;
  const waiting = agent.waiting ?? 0;
  if (unanswered > 0) out.push(unanswered === 1 ? "1 message for you" : `${unanswered} messages for you`);
  if (waiting > 0) out.push(waiting === 1 ? "Waiting on you for 1 thing" : `Waiting on you for ${waiting} things`);
  return out;
}

export function needsYou(agent: Pick<AgentSummary, "waiting" | "unanswered" | "retired">): boolean {
  return waitingOnYou(agent).length > 0;
}

export const FILTERS = ["all", "you", "awake", "resting", "asleep"] as const;
export type Filter = (typeof FILTERS)[number];
export const FILTER_LABELS: Record<Filter, string> = {
  all: "Everyone",
  you: "Waiting on you",
  awake: "Awake",
  resting: "Resting",
  asleep: "Asleep",
};

export function matchesFilter(agent: Seen, filter: Filter): boolean {
  if (filter === "all") return true;
  if (filter === "you") return needsYou(agent);
  const state = describeDoing(agent).state;
  if (filter === "awake") return state === "awake" || state === "new";
  return state === filter;
}

export function filterCounts(agents: readonly Seen[]): Record<Filter, number> {
  const counts = { all: 0, you: 0, awake: 0, resting: 0, asleep: 0 };
  for (const agent of agents) for (const filter of FILTERS) if (matchesFilter(agent, filter)) counts[filter] += 1;
  return counts;
}

const RANK: Record<Doing["state"], number> = { awake: 1, new: 1, resting: 2, asleep: 3, retired: 4 };

/** Whoever is waiting on the owner first, then awake, resting, asleep; arrival order within each. */
export function sortRoster<T extends Seen>(agents: readonly T[]): T[] {
  const rank = (agent: T) => (agent.retired ? 5 : needsYou(agent) ? 0 : RANK[describeDoing(agent).state]);
  return agents.map((agent, index) => ({ agent, index })).sort((a, b) => rank(a.agent) - rank(b.agent) || a.index - b.index).map((entry) => entry.agent);
}

export const COLOURS = ["a", "b", "c", "d"] as const;
export type Colour = (typeof COLOURS)[number];

/**
 * Each agent's colour, from its place in the order everyone arrived (retired
 * agents included), so a colour never moves to someone else when an agent leaves.
 */
export function colourOf(id: string, everyone: readonly Pick<AgentSummary, "id">[]): Colour {
  const at = everyone.findIndex((agent) => agent.id === id);
  return COLOURS[(at < 0 ? 0 : at) % COLOURS.length] as Colour;
}

/** The name it chose, or its working name ("Agent A") until it has one. */
export function displayName(agent: Pick<AgentSummary, "id" | "name" | "role">): string {
  return agentLabel(agent).title;
}

/** The quiet line under the name: its role, and that it has not chosen a name yet. */
export function roleLine(agent: Pick<AgentSummary, "id" | "name" | "role">): string {
  const label = agentLabel(agent);
  if (label.named) return label.role ?? "No role set";
  return label.role ? `${label.role} · ${label.note}` : `No role yet · ${label.note}`;
}

/** One or two letters for the agent's badge. */
export function initials(agent: Pick<AgentSummary, "id" | "name" | "role">): string {
  return initialsOf(displayName(agent));
}

/** Is the village full? `cap` is null when the setting could not be read. */
export function headcount(living: number, cap: number | null): { full: boolean; text: string } {
  if (cap === null) return { full: false, text: `${living} living here` };
  return { full: living >= cap, text: `${living} of ${cap} living here` };
}
