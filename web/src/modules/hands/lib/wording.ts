// Plain words for what the server reports: effects, where a classification came from,
// the three levels, a provider's health, a rule's state and how a run ended.

import type { Dependents, Effect, EffectFrom, Level, ProviderHealth, Provider, ProviderType, Rule, RuleState, Run, RunStatus } from "../api.ts";

export type Tone = "ok" | "warn" | "bad" | "none";

// --- effects -------------------------------------------------------------------

export const EFFECT_LABELS: Record<Effect, string> = {
  read: "Reads",
  send: "Sends",
  create: "Creates",
  update: "Changes",
  delete: "Deletes",
  spend: "Spends money",
  access: "Grants access",
  change: "Changes something",
};

export const EFFECT_SENTENCES: Record<Effect, string> = {
  read: "It only looks. Nothing is changed or sent.",
  send: "It sends something to someone outside.",
  create: "It creates something new.",
  update: "It changes something that already exists.",
  delete: "It deletes something.",
  spend: "It spends money.",
  access: "It gives someone access to something.",
  change: "It changes something, and nobody knows exactly what kind of change.",
};

/** Effects that leave the machine or cannot be taken back lightly: the screen points them out. */
export const CAUTIOUS: readonly Effect[] = ["send", "delete", "spend", "access", "change"];

export function effectLabel(effect: Effect): string {
  return EFFECT_LABELS[effect] ?? "Changes something";
}

export function effectSentence(effect: Effect): string {
  return EFFECT_SENTENCES[effect] ?? EFFECT_SENTENCES.change;
}

export function isReading(effect: Effect): boolean {
  return effect === "read";
}

/** Where a classification came from, in a few words. */
export function sourceLabel(from: EffectFrom): string {
  switch (from) {
    case "provider":
      return "said by the provider";
    case "adapter":
      return "set by the connector";
    case "owner":
      return "corrected by you";
    default:
      return "a guess";
  }
}

/** The same, as a sentence that says how far to trust it. */
export function sourceSentence(from: EffectFrom): string {
  switch (from) {
    case "provider":
      return "The provider says so. That is a hint, not a promise: it only picks the starting level.";
    case "adapter":
      return "JimmyHQ's connector for this provider says so. It picks the starting level and nothing more.";
    case "owner":
      return "You set this yourself.";
    default:
      return "Nobody said: this is a guess. Check it, and correct it if it is wrong.";
  }
}

/** True when the owner should look twice at the classification. */
export function isGuess(from: EffectFrom): boolean {
  return from === "guess";
}

// --- levels --------------------------------------------------------------------

export const LEVEL_LABELS: Record<Level, string> = { read: "Read only", ask: "Ask me each time", alone: "Alone" };

/** What a level means for an action with this effect. */
export function levelExplanation(level: Level, effect: Effect): string {
  const reads = isReading(effect);
  if (level === "read") {
    return reads
      ? "The agent may look things up. It cannot change anything with this action."
      : "This action changes things, so the agent is not offered it at all while it is set to read only. The setting is kept for later.";
  }
  if (level === "ask") {
    return "Each time the agent wants this, a card appears on the Bench showing exactly what would be sent. Approving it does precisely that, once. Nothing happens until you approve.";
  }
  return reads
    ? "The agent does this by itself. Past a limit below, if you set one, it waits for your yes instead."
    : "The agent does this by itself. Limits below are optional: if you set one, anything outside it waits for your yes on the Bench.";
}

const CAUTIOUS_VERB: Partial<Record<Effect, string>> = { send: "sends something to someone", delete: "deletes something", spend: "spends money", access: "changes who has access" };

/** The one sentence shown with "Alone" for a kind of action the owner's list is careful with. It explains; it asks nothing. */
export function cautiousAloneNote(effect: Effect, cautious: boolean | undefined): string | null {
  if (!cautious || isReading(effect)) return null;
  const what = CAUTIOUS_VERB[effect] ?? "changes something outside";
  return `This action ${what}. On "Alone" that happens without a question first. Each time, a card on the Bench tells you afterwards, with Undo where the provider can undo.`;
}

/** The line that suggests "Alone" after enough approvals in a row. It suggests: only the owner changes a level. */
export function suggestionLine(rule: Pick<Rule, "suggest_alone" | "approvals_in_a_row" | "level">, agentName: string): string | null {
  if (!rule.suggest_alone || rule.level !== "ask") return null;
  const n = rule.approvals_in_a_row ?? 0;
  return `You approved the last ${n} of these in a row. If you would rather not be asked, you can set it to "Alone" under Edit. Nothing changes unless you do: ${agentName} still asks each time.`;
}

export function levelLabel(level: string): string {
  if (level === "try") return "Tried by you";
  return LEVEL_LABELS[level as Level] ?? level;
}

/** Short form for a chip. */
export function levelShort(level: string): string {
  if (level === "try") return "your test";
  if (level === "read") return "read only";
  if (level === "ask") return "asks first";
  if (level === "alone") return "alone";
  return level;
}

/** The level the owner is shown for an action before choosing: the server's own default. */
export function levelFromServer(level: string | undefined): Level {
  return level === "read" || level === "ask" || level === "alone" ? level : "ask";
}

/**
 * Whether a refusal sentence is about the level the owner chose, so the screen can put it
 * next to the choice rather than at the foot of the form.
 */
export function refusalIsAboutLevel(sentence: string): boolean {
  return /\b(alone|read only|ask me each time|level)\b/i.test(sentence);
}

// --- providers -----------------------------------------------------------------

export function healthTone(provider: Pick<Provider, "enabled" | "health" | "type_known" | "needs_key" | "has_key">): { tone: Tone; text: string } {
  if (!provider.type_known) return { tone: "bad", text: "No connector installed" };
  if (!provider.enabled) return { tone: "none", text: "Switched off" };
  if (provider.needs_key && !provider.has_key) return { tone: "warn", text: "No key yet" };
  return healthPill(provider.health);
}

export function healthPill(health: ProviderHealth): { tone: Tone; text: string } {
  if (health.ok === null) return { tone: "none", text: "Not tested" };
  return health.ok ? { tone: "ok", text: "Reachable" } : { tone: "bad", text: "Not reachable" };
}

/** What a provider type is, in plain words: the server's description, then its caution. */
export function typeAbout(type: ProviderType | undefined): string {
  if (!type) return "This install has no connector for this type of provider, so nothing can be done with it until one is installed.";
  const said = [type.description?.trim(), type.note?.trim()].filter(Boolean).join(" ");
  return said || `${type.label} lets your agents use an outside service. Nothing is switched on for any agent until you do it.`;
}

/** Where a type's secrets and sign-in tokens live: what the screen says before the owner types a key. */
export function secretsSentence(type: ProviderType | undefined): string {
  const key = type?.needs_key ? "Its key is saved in this computer's secret store and is never shown again. " : "";
  const tokens = type?.tokens_note?.trim();
  if (tokens) return `${key}${tokens}`;
  return `${key}Some providers sign you in on their own page and hold the sign-in themselves. For those, JimmyHQ never sees your password or the sign-in tokens: it keeps only a reference to the account.`;
}

/** What the owner reads while a redirect is under way. */
export const TOKENS_AT_PROVIDER = "Sign in on the provider's page. The provider holds the sign-in, not this computer. JimmyHQ keeps only a reference to the account, and you can cancel any time.";

/** The same, in the type's own words when it says where the tokens live. */
export function redirectSentence(type: ProviderType | undefined): string {
  return type?.tokens === "provider" && type.tokens_note.trim() ? `${type.tokens_note.trim()} You can cancel any time.` : TOKENS_AT_PROVIDER;
}

export function typeLabel(types: readonly ProviderType[] | undefined, type: string): string {
  return types?.find((item) => item.type === type)?.label ?? type;
}

// --- rules ---------------------------------------------------------------------

export function ruleStateLine(state: RuleState): { tone: Tone; text: string } {
  switch (state) {
    case "on":
      return { tone: "ok", text: "On" };
    case "changed":
      return { tone: "warn", text: "Changed upstream: review needed" };
    case "account_gone":
      return { tone: "bad", text: "Account not connected" };
    case "provider_off":
      return { tone: "warn", text: "Provider not ready" };
    case "unreachable":
      return { tone: "warn", text: "Provider not reachable" };
    case "action_gone":
      return { tone: "bad", text: "No longer offered" };
    case "cannot_offer":
      return { tone: "warn", text: "Cannot be offered yet" };
    default:
      return { tone: "none", text: String(state) };
  }
}

/** What to do about a rule that is not on. */
export function ruleStateAdvice(rule: Pick<Rule, "state" | "reason">): string | null {
  switch (rule.state) {
    case "on":
      return null;
    case "changed":
      return "The provider changed this action after you switched it on, so the agent is not offered it. Open it, read what it does now, and save it again to switch it back on.";
    case "action_gone":
      return "The provider no longer has this action. Remove it, or check the account.";
    case "account_gone":
      return "Connect the account again under Settings, Integrations, or remove this rule.";
    default:
      return rule.reason ?? "The agent is not offered this action right now.";
  }
}

// --- runs ----------------------------------------------------------------------

export const RUN_STATUS: Record<RunStatus, { tone: Tone; text: string }> = {
  done: { tone: "ok", text: "Done" },
  failed: { tone: "bad", text: "Failed" },
  unknown: { tone: "warn", text: "Outcome unknown" },
  started: { tone: "none", text: "Under way" },
};

export function runStatus(status: string): { tone: Tone; text: string } {
  return RUN_STATUS[status as RunStatus] ?? { tone: "none", text: status };
}

/** Why a run ended badly, in a few plain words. */
export function runProblem(run: Pick<Run, "status" | "error_kind">): string | null {
  if (run.status === "unknown") {
    return "The provider did not answer, so it may or may not have happened. It is never tried again by itself. Check at the provider.";
  }
  if (run.status !== "failed") return null;
  switch (run.error_kind) {
    case "provider":
      return "The provider said no.";
    case "timeout":
      return "The provider took too long to answer.";
    case "interrupted":
      return "It was cut short.";
    default:
      return run.error_kind ? `It did not work (${run.error_kind.replace(/_/g, " ")}).` : "It did not work.";
  }
}

/** A rule's limits as one short line, or null when it has none. */
export function limitsLine(rule: Pick<Rule, "limits">, amountName?: string | null): string | null {
  const parts: string[] = [];
  const { per_day, recipients, max_amount } = rule.limits;
  if (per_day != null) parts.push(`${per_day} a day`);
  if (recipients === "owner") parts.push("only to you");
  else if (recipients === "known") parts.push("only to people you know");
  else if (recipients === "anyone") parts.push("to anyone");
  if (max_amount != null) parts.push(`up to ${max_amount}${amountName ? ` (${amountName})` : ""}`);
  return parts.length ? parts.join(", ") : null;
}

// --- forgetting ------------------------------------------------------------------

/** What forgetting an account stops, said before the owner confirms. `uses` is the server's answer, or undefined while unknown. */
export function forgetMessage(accountLabel: string, providerLabel: string | null, uses: Pick<Dependents, "agents" | "rules" | "waiting_requests"> | undefined): string {
  let stops: string;
  if (uses === undefined) {
    stops = "Any action an agent has switched on for it stops working at once, and its rule is deleted.";
  } else if (uses.rules === 0) {
    stops = "No agent has an action switched on for it, so nothing stops working.";
  } else {
    const agents = uses.agents.map((item) => item.agent);
    const things = uses.rules === 1 ? "1 switched-on action" : `${uses.rules} switched-on actions`;
    stops = `${things} for ${agents.length === 1 ? "1 agent" : `${agents.length} agents`} (${agents.join(", ")}) stop${uses.rules === 1 ? "s" : ""} working at once, and ${uses.rules === 1 ? "its rule is" : "their rules are"} deleted.`;
  }
  const count = uses?.waiting_requests;
  const waiting =
    count === undefined
      ? "Anything it has put on the Bench for your yes can no longer be carried out."
      : count === 0
        ? "Nothing of it is waiting on the Bench for your yes."
        : `${count === 1 ? "1 request" : `${count} requests`} waiting on the Bench for your yes can no longer be carried out.`;
  const where = providerLabel ? `${providerLabel}` : "the provider";
  return `Forget "${accountLabel}"? ${stops} ${waiting} What JimmyHQ keeps privately about the account is deleted for good. This does not sign you out at ${where}: end the access on its own page too if you want that.`;
}

/** What removing a provider with accounts says. */
export function removeBlockedMessage(providerLabel: string, accounts: readonly string[]): string {
  return `${providerLabel} still has ${accounts.length === 1 ? "an account" : "accounts"} connected (${accounts.join(", ")}). Forget ${accounts.length === 1 ? "it" : "them"} first, then remove the provider.`;
}

// --- removing a saved key or secret ----------------------------------------------

export type Question = { title: string; message: string };

/** What stops when a provider loses a value it needs. `accounts` are the labels of its accounts, or undefined while unknown. */
function accountsStop(providerLabel: string, accounts: readonly string[] | undefined, until: string): string {
  if (accounts === undefined) return `Any account connected to ${providerLabel} stops working ${until}.`;
  if (accounts.length === 0) return `No account is connected to ${providerLabel} yet, and none can be until ${until.replace(/^until /, "")}.`;
  return accounts.length === 1 ? `Its account (${accounts[0]}) stops working ${until}.` : `Its ${accounts.length} accounts (${accounts.join(", ")}) stop working ${until}.`;
}

/** Asked before a provider's saved key is removed: what goes, and what stops working. */
export function removeKeyQuestion(providerLabel: string, accounts: readonly string[] | undefined): Question {
  return {
    title: `Remove the key for ${providerLabel}?`,
    message: `The saved key is deleted from this computer. It is never shown, so it cannot be brought back from here: you would have to paste it again. ${accountsStop(providerLabel, accounts, "until you save another key")}`,
  };
}

/** Asked before one of a provider's other saved secrets is removed. `wanted` is whether the provider's record refers to it. */
export function removeSecretQuestion(providerLabel: string, secret: string, wanted: boolean, accounts: readonly string[] | undefined): Question {
  const gone = `The saved value is deleted from this computer. It is never shown, so it cannot be brought back from here: you would have to paste it again.`;
  const stops = wanted
    ? `${providerLabel} needs it and cannot be used without it. ${accountsStop(providerLabel, accounts, "until you save it again")}`
    : `The record for ${providerLabel} does not refer to it. If anything else still relies on it, that stops working until you save it again.`;
  return { title: `Remove the secret "${secret}"?`, message: `${gone} ${stops}` };
}
