// One rule's form: its state, the request it becomes and the checks the server also makes.
// Mirrors server/jimmyhq/hands/rules.py (check_limits, check_arguments) and service.set_rule.

import { RECIPIENT_LIMITS } from "./vocab.ts";
import type { ActionInfo, Effect, Level, Preflight, PreflightBody, RecipientLimit, Rule, RuleBody } from "../api.ts";
import { argsBody, argsDraft, argumentRows } from "./args.ts";
import type { ArgsDraft, ArgRow } from "./args.ts";
import { levelFromServer } from "./wording.ts";

export const MAX_PER_DAY = 100_000;
export const MAX_FIELDS = 50;
export const MAX_FIELD_CHARS = 200;

export type RuleDraft = {
  level: Level;
  args: ArgsDraft;
  /** Whether the agent sees email addresses and phone numbers in answers. */
  showAddresses: boolean;
  /** One field name per line; empty shows everything the server lets through. */
  fields: string;
  perDay: string;
  recipients: RecipientLimit | "";
  maxAmount: string;
  /** The effect as the owner has it now. Equal to the action's own unless corrected. */
  effect: Effect;
  /** The owner ticked "I understand" for letting a changing action run alone. */
  confirmed: boolean;
};

/** A new rule's starting form: the server's own defaults for this action. */
export function newDraft(action: ActionInfo, ownInbox = false): RuleDraft {
  const rows = argumentRows(action.arguments);
  return {
    level: levelFromServer(action.default_level),
    args: argsDraft(rows, action.default_arguments),
    showAddresses: ownInbox,
    fields: "",
    perDay: "",
    recipients: "",
    maxAmount: "",
    effect: action.effect,
    confirmed: false,
  };
}

/** The form for a rule that is already saved. */
export function draftFromRule(rule: Rule, action: ActionInfo | undefined): RuleDraft {
  const rows = argumentRows(action?.arguments);
  return {
    level: rule.level,
    args: argsDraft(rows, rule.arguments),
    showAddresses: rule.show_addresses,
    fields: (rule.fields ?? []).join("\n"),
    perDay: rule.limits?.per_day == null ? "" : String(rule.limits.per_day),
    recipients: rule.limits?.recipients ?? "",
    maxAmount: rule.limits?.max_amount == null ? "" : String(rule.limits.max_amount),
    effect: rule.effect,
    confirmed: rule.confirmed ?? false,
  };
}

export function fieldList(text: string): string[] {
  return text.split("\n").map((line) => line.trim()).filter(Boolean);
}

export type DraftErrors = { args?: Record<string, string>; perDay?: string; recipients?: string; maxAmount?: string; fields?: string };

/** Everything the server would refuse about the limits and fields, said before it is asked. */
export function validateDraft(action: ActionInfo, draft: RuleDraft): DraftErrors {
  const errors: DraftErrors = {};
  const rows = argumentRows(action.arguments);
  const args = argsBody(rows, draft.args);
  if (!args.ok) errors.args = args.errors;

  const perDay = draft.perDay.trim();
  if (perDay !== "") {
    const n = Number(perDay);
    if (!Number.isInteger(n) || n < 1 || n > MAX_PER_DAY) errors.perDay = `The daily limit must be a whole number from 1 to ${MAX_PER_DAY}.`;
  }
  if (draft.recipients !== "") {
    if (!(RECIPIENT_LIMITS as readonly string[]).includes(draft.recipients)) errors.recipients = "Choose who may receive it.";
    else if (!action.limits_offered.recipients) errors.recipients = "This action does not say who receives it, so it cannot be limited by recipient.";
  }
  const most = draft.maxAmount.trim();
  if (most !== "") {
    const n = Number(most);
    if (!Number.isFinite(n) || n < 0) errors.maxAmount = "The largest amount must be a number, 0 or more.";
    else if (!action.limits_offered.max_amount) errors.maxAmount = "This action does not carry an amount, so it cannot be limited by amount.";
  }
  const fields = fieldList(draft.fields);
  if (fields.length > MAX_FIELDS || fields.some((f) => f.length > MAX_FIELD_CHARS)) {
    errors.fields = `List at most ${MAX_FIELDS} field names, each under ${MAX_FIELD_CHARS} characters.`;
  }
  return errors;
}

export function hasErrors(errors: DraftErrors): boolean {
  return Boolean(errors.perDay || errors.recipients || errors.maxAmount || errors.fields || (errors.args && Object.keys(errors.args).length > 0));
}

/**
 * The request that saves the rule. Always whole: every argument, every limit. The
 * effect is sent only when the owner changed it, so the provider's own stays the
 * provider's. `version` pins what the owner looked at.
 */
export function ruleBody(agent: string, account: string, action: ActionInfo, draft: RuleDraft): RuleBody | null {
  const rows = argumentRows(action.arguments);
  const args = argsBody(rows, draft.args);
  if (!args.ok) return null;
  const perDay = draft.perDay.trim();
  const most = draft.maxAmount.trim();
  const body: RuleBody = {
    agent,
    account,
    action: action.name,
    level: draft.level,
    arguments: args.body,
    limits: {
      per_day: perDay === "" ? null : Number(perDay),
      recipients: draft.recipients === "" ? null : draft.recipients,
      max_amount: most === "" ? null : Number(most),
    },
    show_addresses: draft.showAddresses,
    fields: fieldList(draft.fields),
    version: action.version,
    confirmed: draft.confirmed,
  };
  if (draft.effect !== action.effect) body.effect = draft.effect;
  return body;
}

/**
 * The question put to the server before saving: which levels may this rule take? It
 * carries what the answer can depend on (the effect as corrected, the limits, the
 * confirmation) and nothing else. Limits that would be refused anyway are left out, so a
 * half-typed number does not turn the answer into an error.
 */
export function preflightBody(agent: string, account: string, action: ActionInfo, draft: RuleDraft, errors: DraftErrors = validateDraft(action, draft)): PreflightBody {
  const body: PreflightBody = { agent, account, action: action.name, confirmed: draft.confirmed };
  if (draft.effect !== action.effect) body.effect = draft.effect;
  const perDay = draft.perDay.trim();
  const most = draft.maxAmount.trim();
  body.limits = {
    per_day: perDay === "" || errors.perDay ? null : Number(perDay),
    recipients: draft.recipients === "" || errors.recipients ? null : draft.recipients,
    max_amount: most === "" || errors.maxAmount ? null : Number(most),
  };
  return body;
}

/** The server's reason for each level the rule may not take, by level. */
export function refusedLevels(answer: Pick<Preflight, "levels"> | undefined): Partial<Record<Level, string>> {
  const out: Partial<Record<Level, string>> = {};
  for (const level of ["read", "ask", "alone"] as const) {
    const said = answer?.levels?.[level];
    if (said && !said.allowed) out[level] = said.reason ?? "This level cannot be chosen for this action.";
  }
  return out;
}

/** True when the form is as the saved rule has it, so there is nothing to save. */
export function sameAsSaved(rule: Rule, action: ActionInfo, draft: RuleDraft): boolean {
  const body = ruleBody(rule.agent, rule.account, action, draft);
  if (!body) return false;
  if (rule.version !== action.version) return false;
  if (rule.level !== body.level || rule.show_addresses !== body.show_addresses) return false;
  if (rule.effect !== draft.effect) return false;
  if (JSON.stringify(rule.fields ?? []) !== JSON.stringify(body.fields)) return false;
  const have = rule.limits ?? { per_day: null, recipients: null, max_amount: null };
  if (have.per_day !== (body.limits?.per_day ?? null) || have.recipients !== (body.limits?.recipients ?? null) || have.max_amount !== (body.limits?.max_amount ?? null)) return false;
  for (const [name, choice] of Object.entries(body.arguments ?? {})) {
    const saved = rule.arguments?.[name];
    if (!saved || saved.mode !== choice.mode) return false;
    if (choice.mode === "fixed" && JSON.stringify(saved.value) !== JSON.stringify(choice.value)) return false;
  }
  return true;
}

/** Rows whose open choice the agent could not fill in: the form lists them first. */
export function awkwardRows(rows: readonly ArgRow[]): ArgRow[] {
  return rows.filter((row) => row.kind === null);
}

/** What the limits section offers for this action, as sentences the screen can show. */
export function limitsOffered(action: ActionInfo): { perDay: true; recipients: boolean; amount: boolean } {
  return { perDay: true, recipients: action.limits_offered.recipients, amount: action.limits_offered.max_amount };
}

/** How many more actions this agent may hold, or null when the server did not say. */
export function slotsLeft(held: number, most: number | undefined): number | null {
  return typeof most === "number" ? Math.max(0, most - held) : null;
}
