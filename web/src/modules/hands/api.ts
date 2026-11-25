// The integrations (hands) module's API client. Shapes follow
// server/jimmyhq/hands/routes.py and docs/hands.md. No response holds a key: only
// whether one is saved.

import { request } from "../../api";
import { EFFECTS, LEVELS, RECIPIENT_LIMITS } from "./lib/vocab";
import type { Effect, Level, RecipientLimit } from "./lib/vocab";

export { EFFECTS, LEVELS, RECIPIENT_LIMITS };
export type { Effect, Level, RecipientLimit };
/** Where an action's effect came from. Only "owner" is a decision; the rest are suggestions. */
export type EffectFrom = "provider" | "adapter" | "guess" | "owner";
export type ArgMode = "open" | "fixed" | "hidden";

/** One thing a provider's record takes, as the server's type declares it. */
export type RecordField = {
  /** "base_url", or the option's name. */
  name: string;
  label: string;
  /** "argv" is a program's arguments, one a line; "names" lists names whose values are saved as secrets. */
  kind: "text" | "number" | "path" | "argv" | "names";
  help: string;
  required: boolean;
  where: "record" | "options";
  /** For "names": a value is saved as the secret named this plus the name in lower case. */
  secret_prefix: string | null;
  /** Shown only for a record that is a program on this machine, or only for one that is an address. */
  only_for: "program" | "address" | null;
};

export type ProviderType = {
  type: string;
  label: string;
  needs_key: boolean;
  needs_address: boolean;
  note: string;
  /** What this type of provider is, in plain words. */
  description: string;
  fields: RecordField[];
  /** Where the sign-in tokens of its accounts live: with the provider, on this computer, or nowhere (no sign-in). */
  tokens: "provider" | "here" | "none";
  tokens_note: string;
  /** Whether what this type says an action does may start a reading at "Alone". */
  trusted_hints: boolean;
};
export type TypesResponse = { types: ProviderType[] };

export type ProviderHealth = { ok: boolean | null; checked_ms: number | null; error: string | null };

export type Provider = {
  id: string;
  type: string;
  label: string;
  base_url: string | null;
  enabled: boolean;
  options: Record<string, unknown>;
  has_key: boolean;
  needs_key: boolean;
  /** Names of other saved secrets, never their values. */
  secrets: string[];
  /** Names of the secrets the record refers to, saved or not. */
  secrets_wanted: string[];
  /** False when this install has no adapter for the provider's type. */
  type_known: boolean;
  health: ProviderHealth;
};

export type ProviderBody = { id?: string; type: string; label: string; base_url?: string | null; enabled?: boolean; options?: Record<string, unknown> };
export type ProviderChange = { label?: string; base_url?: string | null; enabled?: boolean; options?: Record<string, unknown> };

export type CheckResult = { ok: boolean; error: string | null; checked_ms: number };

export type Account = {
  account: string;
  provider_id: string;
  label: string;
  app: string | null;
  own_inbox: boolean;
  connected_ms?: number;
  provider_label: string | null;
  /** False when the provider's reference for it is missing: connect it again. */
  connected: boolean;
  receives: boolean;
  /** The account's own address, for an agent's inbox: shown to you only. */
  address?: string | null;
};

export type Connecting = { account: string; provider_id: string; label: string; started_ms: number };
export type AccountsResponse = { accounts: Account[]; connecting: Connecting[] };

export type ConnectBody = { provider_id: string; account: string; label?: string; app?: string | null; own_inbox?: boolean };
export type ConnectStatus = "ready" | "redirect" | "needs_key" | "waiting";
export type ConnectAnswer = { status: ConnectStatus; url: string | null; account: Account | null };

/** A JSON Schema, as little of it as the screen reads. */
export type Schema = {
  type?: string | string[];
  description?: string;
  enum?: unknown[];
  items?: Schema;
  properties?: Record<string, Schema>;
  required?: string[];
  [key: string]: unknown;
};

export type ArgChoice = { mode: ArgMode; value: unknown };

export type ActionInfo = {
  name: string;
  label: string;
  description: string;
  arguments: Schema;
  effect: Effect;
  effect_from: EffectFrom;
  recipients: string[];
  amount: string | null;
  undo: boolean;
  version: string;
  tool: string;
  can_offer: boolean;
  cannot_offer_reason: string | null;
  default_arguments: Record<string, ArgChoice>;
  default_level: Level;
  /** False when the effect is somebody's word nobody checked: a reading then starts at "ask". */
  effect_trusted: boolean;
  /** What to tell the owner when a reading does not start at "alone", or null. */
  level_note: string | null;
  /** A kind of action the owner's own list starts at "ask". It picks a default and a sentence; any level can still be chosen. */
  cautious?: boolean;
  limits_offered: { per_day: boolean; recipients: boolean; max_amount: boolean };
};

export type Limits = { per_day: number | null; recipients: RecipientLimit | null; max_amount: number | null };

/** What a rule can be right now. Anything but "on" means the tool is not offered. */
export type RuleState = "on" | "account_gone" | "provider_off" | "unreachable" | "action_gone" | "changed" | "cannot_offer";

export type Rule = {
  agent: string;
  account: string;
  action: string;
  tool: string;
  label: string;
  level: Level;
  effect: Effect;
  effect_from: EffectFrom;
  version: string;
  arguments: Record<string, ArgChoice>;
  show_addresses: boolean;
  fields: string[];
  limits: Limits;
  confirmed: boolean;
  state: RuleState;
  reason: string | null;
  /** Calls made today, for the daily limit. */
  today: number;
  cautious?: boolean;
  /** How many requests in a row the owner approved, and whether that is enough to suggest "Alone". A suggestion only. */
  approvals_in_a_row?: number;
  suggest_after?: number;
  suggest_alone?: boolean;
};

export type RulesResponse = { rules: Rule[]; max_tools_per_agent: number };

export type RuleBody = {
  agent: string;
  account: string;
  action: string;
  level?: Level;
  arguments?: Record<string, ArgChoice>;
  limits?: Partial<Limits>;
  show_addresses?: boolean;
  fields?: string[];
  /** Only to correct the effect; leave it out to keep the provider's. */
  effect?: Effect;
  /** The version the owner looked at. The server refuses it if the action has changed since. */
  version?: string;
  confirmed?: boolean;
};

export type PreflightBody = { agent: string; account: string; action: string; effect?: Effect; limits?: Partial<Limits>; confirmed?: boolean };
export type LevelAnswer = { allowed: boolean; reason: string | null };
/** Which levels a rule may take for this agent and action, asked before saving. */
export type Preflight = {
  agent: string;
  account: string;
  action: string;
  effect: Effect;
  effect_from: EffectFrom;
  effect_trusted: boolean;
  default_level: Level;
  level_note: string | null;
  cautious?: boolean;
  levels: Record<Level, LevelAnswer>;
};

export type DependentRule = { action: string; tool: string; label: string; level: Level; effect: Effect };
/** Who depends on an account: what forgetting it stops. */
export type Dependents = { account: string; agents: { agent: string; rules: DependentRule[] }[]; rules: number; waiting_requests: number };

export type RunStatus = "started" | "done" | "failed" | "unknown";
/** A request that waited for the owner, as its Bench card shows it. `arguments` is null once it was answered: the values were erased. */
export type RequestValues = {
  card_id: string;
  agent: string;
  account: string;
  action: string;
  tool: string;
  waiting: boolean;
  arguments: Record<string, unknown> | null;
};

export type Run = {
  id: string;
  agent: string;
  account: string;
  provider_id: string;
  action: string;
  tool: string;
  effect: Effect;
  /** read, ask or alone; "try" for the owner's own test. */
  level: string;
  status: RunStatus;
  started_ms: number;
  done_ms: number | null;
  error_kind: string | null;
  card_id: string | null;
  unknown_card: string | null;
  /** The card that says it was done, with Undo where the provider can undo. */
  done_card?: string | null;
  /** The one card to open for this run: what it left on the Bench. */
  card?: string | null;
};

export type TryResult = { ok: boolean; text: string; more: boolean; run_id: string | null };

const BASE = "/api/hands";
const enc = encodeURIComponent;

export const handsApi = {
  types: () => request<TypesResponse>("GET", `${BASE}/types`),
  providers: () => request<{ providers: Provider[] }>("GET", `${BASE}/providers`),
  createProvider: (body: ProviderBody) => request<{ provider: Provider }>("POST", `${BASE}/providers`, { body }),
  updateProvider: (id: string, body: ProviderChange) => request<{ provider: Provider }>("PUT", `${BASE}/providers/${enc(id)}`, { body }),
  removeProvider: (id: string) => request<{ ok: true }>("DELETE", `${BASE}/providers/${enc(id)}`),
  /** Saves a provider's key. It is never read back. */
  setKey: (id: string, key: string) => request<{ id: string; has_key: true }>("PUT", `${BASE}/providers/${enc(id)}/key`, { body: { key } }),
  clearKey: (id: string) => request<{ id: string; has_key: false }>("DELETE", `${BASE}/providers/${enc(id)}/key`),
  setSecret: (id: string, name: string, value: string) => request<{ id: string; name: string; saved: true }>("PUT", `${BASE}/providers/${enc(id)}/secrets/${enc(name)}`, { body: { key: value } }),
  clearSecret: (id: string, name: string) => request<{ id: string; name: string; saved: false }>("DELETE", `${BASE}/providers/${enc(id)}/secrets/${enc(name)}`),
  check: (id: string) => request<CheckResult>("POST", `${BASE}/providers/${enc(id)}/check`),

  accounts: () => request<AccountsResponse>("GET", `${BASE}/accounts`),
  connect: (body: ConnectBody) => request<ConnectAnswer>("POST", `${BASE}/accounts`, { body }),
  finish: (account: string) => request<ConnectAnswer>("POST", `${BASE}/accounts/${enc(account)}/finish`),
  /** Gives up a connection that was started and never finished. Nothing else is touched. */
  cancelConnect: (account: string) => request<{ ok: true }>("DELETE", `${BASE}/accounts/${enc(account)}/connect`),
  dependents: (account: string) => request<Dependents>("GET", `${BASE}/accounts/${enc(account)}/dependents`),
  /** Forgets an account with its rules and everything kept privately for it. */
  forget: (account: string) => request<{ ok: true }>("DELETE", `${BASE}/accounts/${enc(account)}`),
  actions: (account: string, refresh = false) => request<{ actions: ActionInfo[] }>("GET", `${BASE}/accounts/${enc(account)}/actions${refresh ? "?refresh=true" : ""}`),
  tryRead: (account: string, action: string, args: Record<string, unknown>) => request<TryResult>("POST", `${BASE}/accounts/${enc(account)}/try`, { body: { action, arguments: args } }),

  rules: (agent: string) => request<RulesResponse>("GET", `${BASE}/rules?agent=${enc(agent)}`),
  /** Asks which levels the rule may take, and why not. Saves nothing. */
  preflight: (body: PreflightBody) => request<Preflight>("POST", `${BASE}/rules/preflight`, { body }),
  setRule: (body: RuleBody) => request<{ rule: Rule }>("PUT", `${BASE}/rules`, { body }),
  removeRule: (agent: string, account: string, action: string) => request<{ ok: true }>("DELETE", `${BASE}/rules?agent=${enc(agent)}&account=${enc(account)}&action=${enc(action)}`),

  runs: (agent: string, limit = 30) => request<{ runs: Run[] }>("GET", `${BASE}/runs?agent=${enc(agent)}&limit=${limit}`),

  /** What a waiting request would send, for its Bench card. Owner only. */
  requestValues: (cardId: string) => request<RequestValues>("GET", `${BASE}/requests/by-card/${enc(cardId)}`),
};
