// Arranging what the server sends: rules grouped by account, the searchable list of an
// account's actions, and the recent runs.

import type { Account, ActionInfo, Effect, Provider, Rule, Run } from "../api.ts";
import { EFFECT_LABELS } from "./wording.ts";

export type AccountGroup = {
  account: string;
  /** The account's label, or its short name when it has gone from the list. */
  label: string;
  /** False when the account is no longer connected. */
  present: boolean;
  rules: Rule[];
};

/** The agent's rules grouped under their account, accounts in name order and rules by label. */
export function groupByAccount(rules: readonly Rule[], accounts: readonly Account[] | undefined): AccountGroup[] {
  const known = new Map((accounts ?? []).map((a) => [a.account, a]));
  const groups = new Map<string, AccountGroup>();
  for (const rule of rules) {
    let group = groups.get(rule.account);
    if (!group) {
      const found = known.get(rule.account);
      group = { account: rule.account, label: found?.label || rule.account, present: accounts === undefined ? true : found !== undefined, rules: [] };
      groups.set(rule.account, group);
    }
    group.rules.push(rule);
  }
  for (const group of groups.values()) group.rules.sort((a, b) => a.label.localeCompare(b.label) || a.action.localeCompare(b.action));
  return [...groups.values()].sort((a, b) => a.account.localeCompare(b.account));
}

/** The accounts an agent can be given actions from: connected ones whose provider is on. */
export function usableAccounts(accounts: readonly Account[] | undefined, providers: readonly Provider[] | undefined): Account[] {
  const on = new Map((providers ?? []).map((p) => [p.id, p]));
  return (accounts ?? []).filter((a) => a.connected && (providers === undefined || on.get(a.provider_id)?.enabled !== false)).sort((a, b) => a.account.localeCompare(b.account));
}

/** Words the owner might type to find an action: its label, description, name and what kind of effect it has. */
function haystack(action: ActionInfo): string {
  return [action.label, action.description, action.name, EFFECT_LABELS[action.effect] ?? ""].join(" ").toLowerCase();
}

/** The actions that match every word of the search. An empty search keeps them all, in label order. */
export function filterActions(actions: readonly ActionInfo[], query: string, effect: Effect | "all" = "all"): ActionInfo[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return actions
    .filter((a) => (effect === "all" || a.effect === effect) && words.every((w) => haystack(a).includes(w)))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/** Actions already switched on for this agent on this account. */
export function alreadyOn(rules: readonly Rule[], account: string): Set<string> {
  return new Set(rules.filter((r) => r.account === account).map((r) => r.action));
}

/** Effects present in a list, in the order the screen offers them as a filter. */
export function effectsPresent(actions: readonly ActionInfo[]): Effect[] {
  const order: Effect[] = ["read", "send", "create", "update", "delete", "spend", "access", "change"];
  const have = new Set(actions.map((a) => a.effect));
  return order.filter((e) => have.has(e));
}

/** Runs shown for an agent: its own, newest first. The owner's own tries are left out. */
export function runsOfAgent(runs: readonly Run[], agent: string): Run[] {
  return runs.filter((r) => r.agent === agent).sort((a, b) => b.started_ms - a.started_ms);
}

/** The label of a run's action: from the rule if the agent still has it, else the tool's name. */
export function runLabel(run: Run, rules: readonly Rule[]): string {
  const rule = rules.find((r) => r.account === run.account && r.action === run.action);
  return rule?.label ?? run.tool;
}

/** The Bench card a run points at: the one that says what was done, else the one that reported an unknown outcome, else the one that asked. */
export function runCard(run: Run): string | null {
  return run.card ?? run.done_card ?? run.unknown_card ?? run.card_id ?? null;
}

/** What the link to a run's card says. */
export function runCardWords(run: Run): string {
  if (run.done_card) return "See what was done on the Bench";
  if (run.unknown_card) return "See the note on the Bench";
  return "See the card on the Bench";
}

/** How many of an agent's rules are on, for the tab's summary. */
export function countOn(rules: readonly Rule[]): number {
  return rules.filter((r) => r.state === "on" && (r.effect === "read" || r.level !== "read")).length;
}

/** Rules that need the owner: changed upstream, or no longer offered. */
export function needReview(rules: readonly Rule[]): Rule[] {
  return rules.filter((r) => r.state === "changed");
}

/** Whether a rule's action name is one the account still lists. */
export function findAction(actions: readonly ActionInfo[] | undefined, name: string): ActionInfo | undefined {
  return actions?.find((a) => a.name === name);
}
