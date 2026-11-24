// Provider kinds, the add/edit form's draft, and how a provider is described.

import type { Effort, Health, Provider, ProviderChange, ProviderCreate, ProviderOptions, ProviderTest, ProviderType } from "../api.ts";

/** Reads the `local` flag of a provider, health entry or reply. */
export function isLocal(item: { local: boolean }): boolean {
  const { local } = item;
  return local;
}

/** What the owner picks in the dialog. Several kinds share one server type. */
export type ProviderKind = "local" | "anthropic" | "openai" | "google" | "bedrock" | "openrouter" | "custom";

export type KindInfo = {
  kind: ProviderKind;
  type: ProviderType;
  title: string;
  hint: string;
  address: "required" | "optional" | "none";
  /** Whether this kind takes a key: "required" to work, "optional", or "none". */
  key: "required" | "optional" | "none";
  defaultLabel: string;
  defaultAddress: string;
};

export const KINDS: readonly KindInfo[] = [
  {
    kind: "local",
    type: "openai_compatible",
    title: "Local or self-hosted endpoint",
    hint: "A model server on this machine or your own network that speaks the OpenAI-compatible API.",
    address: "required",
    key: "optional",
    defaultLabel: "Local endpoint",
    defaultAddress: "",
  },
  {
    kind: "anthropic",
    type: "anthropic",
    title: "Anthropic",
    hint: "Hosted. Needs an API key.",
    address: "optional",
    key: "required",
    defaultLabel: "Anthropic",
    defaultAddress: "",
  },
  {
    kind: "openai",
    type: "openai",
    title: "OpenAI",
    hint: "Hosted. Needs an API key.",
    address: "optional",
    key: "required",
    defaultLabel: "OpenAI",
    defaultAddress: "",
  },
  {
    kind: "google",
    type: "google",
    title: "Google",
    hint: "Hosted. Needs an API key.",
    address: "optional",
    key: "required",
    defaultLabel: "Google",
    defaultAddress: "",
  },
  {
    kind: "bedrock",
    type: "bedrock",
    title: "AWS Bedrock",
    hint: "Hosted. Uses the AWS credentials on the machine JimmyHQ runs on, not a key saved here.",
    address: "none",
    key: "none",
    defaultLabel: "AWS Bedrock",
    defaultAddress: "",
  },
  {
    kind: "openrouter",
    type: "openai_compatible",
    title: "OpenRouter",
    hint: "Hosted. An OpenAI-compatible service that needs an API key.",
    address: "required",
    key: "required",
    defaultLabel: "OpenRouter",
    defaultAddress: "https://openrouter.ai/api/v1",
  },
  {
    kind: "custom",
    type: "openai_compatible",
    title: "Custom",
    hint: "Any other OpenAI-compatible endpoint.",
    address: "required",
    key: "optional",
    defaultLabel: "",
    defaultAddress: "",
  },
];

export function kindInfo(kind: ProviderKind): KindInfo {
  return KINDS.find((item) => item.kind === kind) ?? KINDS[0]!;
}

/** Usual addresses of well-known local servers. Suggestions only: the owner can change them. */
export const LOCAL_SUGGESTIONS: readonly { name: string; address: string }[] = [
  { name: "Ollama", address: "http://127.0.0.1:11434/v1" },
  { name: "LM Studio", address: "http://127.0.0.1:1234/v1" },
  { name: "llama.cpp", address: "http://127.0.0.1:8080/v1" },
  { name: "vLLM", address: "http://127.0.0.1:8000/v1" },
];

/** The kind an existing provider is shown as when it is edited. */
export function kindOf(provider: Pick<Provider, "type" | "local">): ProviderKind {
  if (provider.type !== "openai_compatible") return provider.type;
  return isLocal(provider) ? "local" : "custom";
}

const TYPE_LABELS: Record<ProviderType, string> = {
  openai_compatible: "OpenAI-compatible",
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google",
  bedrock: "AWS Bedrock",
};

export function typeLabel(type: string): string {
  return TYPE_LABELS[type as ProviderType] ?? type;
}

const MONOGRAMS: Partial<Record<ProviderType, string>> = { openai: "OA", anthropic: "AN", google: "GO", bedrock: "BR" };

/** Two letters for the provider's tile. */
export function monogram(provider: Pick<Provider, "type" | "label">): string {
  const fixed = MONOGRAMS[provider.type];
  if (fixed) return fixed;
  const letters = provider.label.replace(/[^\p{L}\p{N}]+/gu, "");
  return (letters.slice(0, 2) || "?").toUpperCase();
}

/** Whether a provider needs a key and has none. The server never contacts one like that. */
export function missingKey(provider: Pick<Provider, "needs_key" | "has_key">): boolean {
  return provider.needs_key && !provider.has_key;
}

export type StatusTone = "ok" | "warn" | "bad" | "none";
export type ProviderStatus = { tone: StatusTone; text: string; error: string | null };

/** The status pill for a provider, from its switch, key and last health check. */
export function providerStatus(provider: Pick<Provider, "label" | "enabled" | "has_key" | "needs_key">, health: Health): ProviderStatus {
  if (!provider.enabled) return { tone: "none", text: "switched off", error: null };
  if (missingKey(provider)) {
    // The server's sentence when it has one; the same words while a stale answer is on screen.
    const told = health.ok === null ? health.error : null;
    return { tone: "none", text: "not set up", error: told ?? `${provider.label} has no key yet. Add one under Settings, Models.` };
  }
  if (health.ok === true) return { tone: "ok", text: "connected", error: null };
  if (health.ok === false) return { tone: "bad", text: "needs attention", error: health.error };
  return { tone: "warn", text: "untested", error: null };
}

/** The muted line under a provider's name. Never contains the key. */
export function providerDetail(provider: Pick<Provider, "type" | "base_url" | "region" | "profile" | "has_key">): string {
  const parts = [typeLabel(provider.type)];
  if (provider.base_url) parts.push(provider.base_url);
  if (provider.type === "bedrock") {
    if (provider.region) parts.push(provider.region);
    parts.push(provider.profile ? `profile ${provider.profile}` : "default AWS credentials");
  } else {
    parts.push(provider.has_key ? "key saved" : "no key");
  }
  return parts.join(" · ");
}

/** The server's answer to a test, in plain words. */
export function testSentence(result: ProviderTest): string {
  if (!result.ok) return result.error ?? "The test failed, and the provider did not say why.";
  if (result.models === null) return "Connected. It has no model list, but it answered a short test message.";
  if (result.models === 0) return "Connected, but it lists no models.";
  return `Connected. ${result.models} ${result.models === 1 ? "model" : "models"} found.`;
}

// --- the add/edit form ---------------------------------------------------------

export type Locality = "auto" | "local" | "hosted";
export type NativeJson = "auto" | "yes" | "no";

export type ProviderDraft = {
  kind: ProviderKind;
  label: string;
  /** Only used when adding. Empty lets the server make one from the label. */
  id: string;
  base_url: string;
  region: string;
  profile: string;
  locality: Locality;
  max_tokens: string;
  temperature: string;
  effort: Effort | "";
  native_json: NativeJson;
};

export function emptyDraft(kind: ProviderKind): ProviderDraft {
  const info = kindInfo(kind);
  return {
    kind,
    label: info.defaultLabel,
    id: "",
    base_url: info.defaultAddress,
    region: "",
    profile: "",
    locality: "auto",
    max_tokens: "",
    temperature: "",
    effort: "",
    native_json: "auto",
  };
}

export function draftFrom(provider: Provider): ProviderDraft {
  const { options } = provider;
  return {
    kind: kindOf(provider),
    label: provider.label,
    id: provider.id,
    base_url: provider.base_url ?? "",
    region: provider.region ?? "",
    profile: provider.profile ?? "",
    locality: isLocal(provider) ? "local" : "hosted",
    max_tokens: options.max_tokens === null ? "" : String(options.max_tokens),
    temperature: options.temperature === null ? "" : String(options.temperature),
    effort: options.effort ?? "",
    native_json: options.native_json === null ? "auto" : options.native_json ? "yes" : "no",
  };
}

/** Switching kind while adding keeps what the owner typed where it still applies. */
export function switchKind(draft: ProviderDraft, kind: ProviderKind): ProviderDraft {
  const before = kindInfo(draft.kind);
  const after = kindInfo(kind);
  const labelUntouched = draft.label.trim() === "" || draft.label === before.defaultLabel;
  const addressUntouched = draft.base_url.trim() === "" || draft.base_url === before.defaultAddress;
  return {
    ...draft,
    kind,
    label: labelUntouched ? after.defaultLabel : draft.label,
    base_url: after.address === "none" ? "" : addressUntouched ? after.defaultAddress : draft.base_url,
    region: after.type === "bedrock" ? draft.region : "",
    profile: after.type === "bedrock" ? draft.profile : "",
  };
}

const ID_PATTERN = /^[a-z][a-z0-9_-]{0,39}$/;
// Control characters have no place in a name; the server refuses them too.
const CONTROL = /[\u0000-\u001f\u007f]/;

/** The id the server will make from a label when none is given. */
export function slugify(label: string): string {
  let made = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!made) return "provider";
  if (!/^[a-z]/.test(made)) made = `p-${made}`;
  return made.slice(0, 40).replace(/-+$/, "");
}

export function addressError(value: string, required: boolean): string | null {
  const text = value.trim();
  if (!text) return required ? "Enter the address, such as http://127.0.0.1:11434/v1." : null;
  if (text.length > 500) return "The address cannot be longer than 500 characters.";
  if (CONTROL.test(text) || /\s/.test(text)) return "The address cannot contain spaces.";
  if (!/^https?:\/\/[^/?#]+/i.test(text)) return "The address must start with http:// or https:// and name a host.";
  if (/[?#]/.test(text)) return "The address cannot contain ? or #. Save a key in the key field instead.";
  const authority = text.replace(/^https?:\/\//i, "").split("/")[0] ?? "";
  if (authority.includes("@")) return "Do not put a name or key in the address. Save the key in the key field instead.";
  return null;
}

export function keyError(value: string): string | null {
  const key = value.trim();
  if (!key) return "Enter the key.";
  if (key.length > 8192) return "The key cannot be longer than 8192 characters.";
  if (!/^[!-~]+$/.test(key)) return "A key cannot contain spaces or unusual characters.";
  return null;
}

export type DraftErrors = Partial<Record<"label" | "id" | "base_url" | "region" | "profile" | "max_tokens" | "temperature", string>>;

export function validateDraft(draft: ProviderDraft, mode: "add" | "edit"): DraftErrors {
  const info = kindInfo(draft.kind);
  const errors: DraftErrors = {};
  const label = draft.label.trim();
  if (!label) errors.label = "Give the provider a name.";
  else if (label.length > 80) errors.label = "The name cannot be longer than 80 characters.";
  else if (CONTROL.test(label)) errors.label = "The name contains characters that cannot be saved.";
  if (mode === "add" && draft.id.trim() && !ID_PATTERN.test(draft.id.trim())) {
    errors.id = "An id starts with a letter and uses only lower-case letters, digits, - and _ (40 at most).";
  }
  if (info.address !== "none") {
    const problem = addressError(draft.base_url, info.address === "required");
    if (problem) errors.base_url = problem;
  }
  if (info.type === "bedrock") {
    const region = draft.region.trim();
    if (!region) errors.region = "Enter the AWS region, such as us-east-1.";
    else if (region.length > 40 || !/^[a-z0-9-]+$/.test(region)) {
      errors.region = "A region uses only lower-case letters, digits and - (40 at most).";
    }
    const profile = draft.profile.trim();
    if (profile.length > 80) errors.profile = "The profile cannot be longer than 80 characters.";
    else if (CONTROL.test(profile)) errors.profile = "The profile contains characters that cannot be saved.";
  }
  const limit = draft.max_tokens.trim();
  if (limit) {
    const value = Number(limit);
    if (!Number.isInteger(value) || value < 1 || value > 1_000_000) errors.max_tokens = "Enter a whole number from 1 to 1000000, or leave it empty.";
  }
  const temperature = draft.temperature.trim();
  if (temperature) {
    const value = Number(temperature);
    if (!Number.isFinite(value) || value < 0 || value > 2) errors.temperature = "Enter a number from 0 to 2, or leave it empty.";
  }
  return errors;
}

export function draftOptions(draft: ProviderDraft): ProviderOptions {
  return {
    max_tokens: draft.max_tokens.trim() ? Number(draft.max_tokens) : null,
    temperature: draft.temperature.trim() ? Number(draft.temperature) : null,
    effort: draft.effort || null,
    native_json: draft.native_json === "auto" ? null : draft.native_json === "yes",
  };
}

function localityValue(locality: Locality): boolean | null {
  return locality === "auto" ? null : locality === "local";
}

function sameOptions(a: ProviderOptions, b: ProviderOptions): boolean {
  return a.max_tokens === b.max_tokens && a.temperature === b.temperature && a.effort === b.effort && a.native_json === b.native_json;
}

/** The body for creating a provider from a valid draft. */
export function createBody(draft: ProviderDraft): ProviderCreate {
  const info = kindInfo(draft.kind);
  const body: ProviderCreate = { type: info.type, label: draft.label.trim() };
  if (draft.id.trim()) body.id = draft.id.trim();
  if (info.address !== "none") body.base_url = draft.base_url.trim() || null;
  if (info.type === "bedrock") {
    body.region = draft.region.trim();
    body.profile = draft.profile.trim() || null;
  }
  if (draft.locality !== "auto") body["local"] = localityValue(draft.locality);
  const options = draftOptions(draft);
  if (!sameOptions(options, { max_tokens: null, temperature: null, effort: null, native_json: null })) body.options = options;
  return body;
}

/**
 * The body for changing a provider: only what differs from what is saved.
 * Leaving the local/hosted choice alone sends nothing for it, so the server
 * works it out again if the address moved.
 */
export function changeBody(draft: ProviderDraft, saved: Provider): ProviderChange {
  const before = draftFrom(saved);
  const info = kindInfo(draft.kind);
  const body: ProviderChange = {};
  if (draft.label.trim() !== saved.label) body.label = draft.label.trim();
  if (info.address !== "none" && draft.base_url.trim().replace(/\/+$/, "") !== (saved.base_url ?? "")) {
    body.base_url = draft.base_url.trim() || null;
  }
  if (info.type === "bedrock") {
    if (draft.region.trim() !== (saved.region ?? "")) body.region = draft.region.trim();
    if (draft.profile.trim() !== (saved.profile ?? "")) body.profile = draft.profile.trim() || null;
  }
  if (draft.locality !== before.locality) body["local"] = localityValue(draft.locality);
  const options = draftOptions(draft);
  if (!sameOptions(options, saved.options)) body.options = options;
  return body;
}

export function isEmptyChange(body: ProviderChange): boolean {
  return Object.keys(body).length === 0;
}
