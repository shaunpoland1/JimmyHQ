// Typed client for the models API (`/api/models`). Shapes mirror what the
// server sends, field for field. A key is never returned: only `has_key`.

import { request } from "../../api.ts";

export type ProviderType = "openai_compatible" | "openai" | "anthropic" | "google" | "bedrock";
export type Effort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

/** Every effort the server accepts, weakest first. */
export const EFFORTS: readonly Effort[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];

export type ProviderOptions = {
  max_tokens: number | null;
  temperature: number | null;
  effort: Effort | null;
  /** null = the provider type decides. */
  native_json: boolean | null;
};

/**
 * `ok` and `checked_ms` null = never checked. `error` is then null too, unless the
 * provider needs a key and has none: it is never contacted, and `error` says to add a key.
 */
export type Health = { ok: boolean | null; checked_ms: number | null; error: string | null };

export type Provider = {
  id: string;
  type: ProviderType;
  label: string;
  base_url: string | null;
  enabled: boolean;
  local: boolean;
  options: ProviderOptions;
  region: string | null;
  profile: string | null;
  has_key: boolean;
  /** Whether this type cannot be used, or even contacted, without a saved key. */
  needs_key: boolean;
  health: Health;
};

export type ProvidersResponse = { providers: Provider[] };
export type ProviderResponse = { provider: Provider };

export type ProviderCreate = {
  id?: string | null;
  type: ProviderType;
  label: string;
  base_url?: string | null;
  enabled?: boolean;
  local?: boolean | null;
  options?: Partial<ProviderOptions> | null;
  region?: string | null;
  profile?: string | null;
};

/** Only the fields sent are changed. `options` replaces the whole object. */
export type ProviderChange = {
  label?: string;
  base_url?: string | null;
  enabled?: boolean;
  local?: boolean | null;
  options?: Partial<ProviderOptions> | null;
  region?: string | null;
  profile?: string | null;
};

export type KeyState = { id: string; has_key: boolean };

export type ProviderTest = { ok: boolean; error: string | null; models: number | null; checked_ms: number };

/** Fails with 409 for a provider that needs a key and has none, 502 when the provider fails. `refresh` only counts for the owner; a read token is not given a fresh try at a provider checked within the health interval. */
export type ProviderModels = { provider_id: string; models: string[]; cached: boolean; fetched_ms: number };

export type Target = { provider_id: string; model: string; effort: Effort | null };
export type TargetInput = { provider_id: string; model: string; effort?: Effort | null };

export type RoleEntry = { role: string; primary: Target | null; fallbacks: Target[]; inherits_default: boolean };
export type RolesResponse = { roles: RoleEntry[] };
export type RoleSaved = { role: string; primary: Target | null; fallbacks: Target[] };
export type RoleBody = { primary: TargetInput | null; fallbacks?: TargetInput[] };

export type HealthEntry = {
  id: string;
  label: string;
  type: ProviderType;
  local: boolean;
  ok: boolean | null;
  checked_ms: number | null;
  error: string | null;
};
/** Enabled providers only, sorted by label. */
export type HealthResponse = { interval_s: number; providers: HealthEntry[] };

export type UsageModelRow = {
  /** Empty string (with an empty model) is the row that gathers every model past the cap. */
  provider_id: string;
  provider_type: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  local_calls: number;
  /** null = hosted calls with no matching price line. */
  estimated_cost: number | null;
};

export type UsageWindow = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  local_calls: number;
  local_input_tokens: number;
  local_output_tokens: number;
  fallback_calls: number;
  failed_calls: number;
  estimated_cost: number;
  unpriced_calls: number;
  /** null when no savings reference is set or found. */
  saved_by_local: number | null;
  models: UsageModelRow[];
};

export type UsageDay = {
  /** "YYYY-MM-DD", a UTC day. */
  date: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  local_calls: number;
  failed_calls: number;
  estimated_cost: number;
};

export type PriceEntry = { id: string; provider: string; model: string; input_per_mtok: number; output_per_mtok: number };

export type UsageResponse = {
  all_time: UsageWindow;
  last_24h: UsageWindow;
  days: UsageDay[];
  currency: string;
  savings_reference: PriceEntry | null;
  prices_are_default: boolean;
};

export type PricesResponse = { prices: PriceEntry[]; is_default: boolean };

export type TryBody =
  | { prompt: string; role: string; local_only?: boolean }
  | { prompt: string; provider_id: string; model: string; local_only?: boolean };

export type TryReply = {
  text: string;
  provider_id: string;
  provider_type: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  duration_ms: number;
  /** "provider_id/model" of the first choice when it did not answer. */
  fallback_from: string | null;
  fallback_reason: string | null;
  local: boolean;
};

export type TryResult =
  | { ok: true; error: null; reason: null; reply: TryReply }
  | { ok: false; error: string; reason: string; reply: null };

const BASE = "/api/models";

function providerPath(id: string, rest = ""): string {
  return `${BASE}/providers/${encodeURIComponent(id)}${rest}`;
}

export const modelsApi = {
  providers: () => request<ProvidersResponse>("GET", `${BASE}/providers`),
  createProvider: (body: ProviderCreate) => request<ProviderResponse>("POST", `${BASE}/providers`, { body }),
  updateProvider: (id: string, body: ProviderChange) => request<ProviderResponse>("PUT", providerPath(id), { body }),
  removeProvider: (id: string) => request<{ ok: true }>("DELETE", providerPath(id)),
  setKey: (id: string, key: string) => request<KeyState>("PUT", providerPath(id, "/key"), { body: { key } }),
  clearKey: (id: string) => request<KeyState>("DELETE", providerPath(id, "/key")),
  testProvider: (id: string) => request<ProviderTest>("POST", providerPath(id, "/test")),
  providerModels: (id: string, refresh = false) =>
    request<ProviderModels>("GET", providerPath(id, `/models?refresh=${refresh ? "true" : "false"}`)),
  roles: () => request<RolesResponse>("GET", `${BASE}/roles`),
  setRole: (role: string, body: RoleBody) => request<RoleSaved>("PUT", `${BASE}/roles/${encodeURIComponent(role)}`, { body }),
  health: () => request<HealthResponse>("GET", `${BASE}/health`),
  usage: () => request<UsageResponse>("GET", `${BASE}/usage`),
  prices: () => request<PricesResponse>("GET", `${BASE}/prices`),
  setPrices: (prices: PriceEntry[]) => request<PricesResponse>("PUT", `${BASE}/prices`, { body: { prices } }),
  tryModel: (body: TryBody) => request<TryResult>("POST", `${BASE}/try`, { body }),
};
