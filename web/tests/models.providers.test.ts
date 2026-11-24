import assert from "node:assert/strict";
import { test } from "node:test";
import type { Provider } from "../src/modules/models/api.ts";
import {
  KINDS,
  LOCAL_SUGGESTIONS,
  addressError,
  changeBody,
  createBody,
  draftFrom,
  emptyDraft,
  keyError,
  kindOf,
  missingKey,
  monogram,
  providerDetail,
  providerStatus,
  slugify,
  switchKind,
  testSentence,
  validateDraft,
} from "../src/modules/models/lib/providers.ts";

const NO_OPTIONS = { max_tokens: null, temperature: null, effort: null, native_json: null };
const NEVER = { ok: null, checked_ms: null, error: null };

function provider(patch: Partial<Provider> = {}): Provider {
  return {
    id: "home-server",
    type: "openai_compatible",
    label: "Home server",
    base_url: "http://127.0.0.1:11434/v1",
    enabled: true,
    local: true,
    options: NO_OPTIONS,
    region: null,
    profile: null,
    has_key: false,
    needs_key: false,
    health: NEVER,
    ...patch,
  };
}

test("every kind maps to a provider type the server knows", () => {
  const types = new Set(["openai_compatible", "openai", "anthropic", "google", "bedrock"]);
  for (const kind of KINDS) assert.ok(types.has(kind.type), kind.kind);
});

test("suggested local addresses are loopback only and pass the address check", () => {
  for (const item of LOCAL_SUGGESTIONS) {
    assert.match(item.address, /^http:\/\/127\.0\.0\.1:\d+\//);
    assert.equal(addressError(item.address, true), null);
  }
});

test("the id made from a label matches the server's rule", () => {
  assert.equal(slugify("Home server"), "home-server");
  assert.equal(slugify("  4 fast ones!  "), "p-4-fast-ones");
  assert.equal(slugify("***"), "provider");
  assert.equal(slugify("a".repeat(60)).length, 40);
});

test("an address must be http or https with a host and no secrets in it", () => {
  assert.equal(addressError("", false), null);
  assert.ok(addressError("", true));
  assert.ok(addressError("ftp://example.com", true));
  assert.ok(addressError("http://", true));
  assert.ok(addressError("https://name:secret@example.com/v1", true));
  assert.ok(addressError("https://example.com/v1?key=abc", true));
  assert.ok(addressError("https://example.com/v1#x", true));
  assert.ok(addressError("http://exa mple.com", true));
  assert.equal(addressError("https://example.com/v1/", true), null);
});

test("a key must be visible characters without spaces", () => {
  assert.equal(keyError("  test-key-123  "), null);
  assert.ok(keyError(""));
  assert.ok(keyError("has space"));
  assert.ok(keyError("café"));
});

test("a draft needs a name, and the fields its type needs", () => {
  assert.ok(validateDraft({ ...emptyDraft("custom") }, "add").label);
  assert.ok(validateDraft({ ...emptyDraft("local"), label: "Mine" }, "add").base_url);
  assert.deepEqual(validateDraft(emptyDraft("anthropic"), "add"), {});
  assert.ok(validateDraft(emptyDraft("bedrock"), "add").region);
  assert.ok(validateDraft({ ...emptyDraft("bedrock"), region: "US East" }, "add").region);
  assert.deepEqual(validateDraft({ ...emptyDraft("bedrock"), region: "us-east-1" }, "add"), {});
});

test("an id, answer limit and temperature are checked only when given", () => {
  const base = { ...emptyDraft("anthropic") };
  assert.ok(validateDraft({ ...base, id: "Bad Id" }, "add").id);
  assert.equal(validateDraft({ ...base, id: "Bad Id" }, "edit").id, undefined);
  assert.ok(validateDraft({ ...base, max_tokens: "1.5" }, "add").max_tokens);
  assert.ok(validateDraft({ ...base, max_tokens: "0" }, "add").max_tokens);
  assert.ok(validateDraft({ ...base, temperature: "3" }, "add").temperature);
  assert.deepEqual(validateDraft({ ...base, max_tokens: "2048", temperature: "0.7" }, "add"), {});
});

test("a new provider's body sends only what the type needs", () => {
  const local = createBody({ ...emptyDraft("local"), label: " Home server ", base_url: " http://127.0.0.1:8080/v1 " });
  assert.deepEqual(local, { type: "openai_compatible", label: "Home server", base_url: "http://127.0.0.1:8080/v1" });
  const cloud = createBody({ ...emptyDraft("bedrock"), region: "us-east-1", locality: "hosted", effort: "low" });
  assert.deepEqual(cloud, {
    type: "bedrock",
    label: "AWS Bedrock",
    region: "us-east-1",
    profile: null,
    local: false,
    options: { max_tokens: null, temperature: null, effort: "low", native_json: null },
  });
  assert.equal(createBody(emptyDraft("anthropic")).base_url, null);
});

test("choosing OpenRouter prefills its address and switching away clears an untouched one", () => {
  const router = switchKind(emptyDraft("local"), "openrouter");
  assert.equal(router.label, "OpenRouter");
  assert.match(router.base_url, /^https:\/\//);
  const custom = switchKind(router, "custom");
  assert.equal(custom.base_url, "");
  const typed = switchKind({ ...emptyDraft("local"), label: "Mine", base_url: "http://127.0.0.1:9000/v1" }, "custom");
  assert.equal(typed.label, "Mine");
  assert.equal(typed.base_url, "http://127.0.0.1:9000/v1");
});

test("an unchanged draft changes nothing, and only edited fields are sent", () => {
  const saved = provider();
  assert.deepEqual(changeBody(draftFrom(saved), saved), {});
  assert.deepEqual(changeBody({ ...draftFrom(saved), label: "Den server" }, saved), { label: "Den server" });
  assert.deepEqual(changeBody({ ...draftFrom(saved), base_url: "http://127.0.0.1:11434/v1/" }, saved), {});
});

test("moving the address without touching local or hosted leaves the server to decide again", () => {
  const saved = provider();
  const body = changeBody({ ...draftFrom(saved), base_url: "https://models.example.com/v1" }, saved);
  assert.deepEqual(body, { base_url: "https://models.example.com/v1" });
  assert.equal("local" in body, false);
});

test("an explicit local or hosted choice is sent, and deciding again sends null", () => {
  const saved = provider();
  assert.deepEqual(changeBody({ ...draftFrom(saved), locality: "hosted" }, saved), { local: false });
  assert.deepEqual(changeBody({ ...draftFrom(saved), locality: "auto" }, saved), { local: null });
});

test("changing one option sends the whole options object", () => {
  const saved = provider({ options: { ...NO_OPTIONS, max_tokens: 1000 } });
  const body = changeBody({ ...draftFrom(saved), temperature: "0.5" }, saved);
  assert.deepEqual(body, { options: { max_tokens: 1000, temperature: 0.5, effort: null, native_json: null } });
});

test("an existing provider is shown as the kind that matches its type and where it runs", () => {
  assert.equal(kindOf(provider()), "local");
  assert.equal(kindOf(provider({ local: false })), "custom");
  assert.equal(kindOf(provider({ type: "google", local: false })), "google");
});

test("the status pill follows the switch, the key and the last health check", () => {
  assert.equal(providerStatus(provider({ enabled: false }), { ok: true, checked_ms: 1, error: null }).text, "switched off");
  assert.equal(providerStatus(provider({ type: "anthropic", needs_key: true }), NEVER).text, "not set up");
  assert.deepEqual(providerStatus(provider(), NEVER), { tone: "warn", text: "untested", error: null });
  assert.equal(providerStatus(provider(), { ok: true, checked_ms: 1, error: null }).tone, "ok");
  assert.deepEqual(providerStatus(provider(), { ok: false, checked_ms: 1, error: "It could not be reached." }), {
    tone: "bad",
    text: "needs attention",
    error: "It could not be reached.",
  });
});

test("the detail line says whether a key is saved and never shows one", () => {
  assert.equal(providerDetail(provider({ has_key: true })), "OpenAI-compatible · http://127.0.0.1:11434/v1 · key saved");
  assert.equal(providerDetail(provider({ type: "anthropic", base_url: null })), "Anthropic · no key");
  assert.equal(providerDetail(provider({ type: "bedrock", base_url: null, region: "us-east-1" })), "AWS Bedrock · us-east-1 · default AWS credentials");
});

test("a provider's tile shows two letters", () => {
  assert.equal(monogram(provider()), "HO");
  assert.equal(monogram(provider({ type: "anthropic" })), "AN");
  assert.equal(monogram(provider({ label: "-" })), "?");
});

test("a test result is put in plain words", () => {
  assert.equal(testSentence({ ok: true, error: null, models: 2, checked_ms: 1 }), "Connected. 2 models found.");
  assert.equal(testSentence({ ok: true, error: null, models: 1, checked_ms: 1 }), "Connected. 1 model found.");
  assert.match(testSentence({ ok: true, error: null, models: null, checked_ms: 1 }), /answered a short test message/);
  assert.equal(testSentence({ ok: false, error: "It could not be reached.", models: null, checked_ms: 1 }), "It could not be reached.");
});

test("a provider that needs a key and has none is not set up, with a sentence saying to add one", () => {
  const hosted = provider({ type: "anthropic", label: "Hosted", local: false, needs_key: true });
  const sentence = "Hosted has no key yet. Add one under Settings, Models.";
  assert.equal(missingKey(hosted), true);
  assert.equal(missingKey({ ...hosted, has_key: true }), false);
  // The server decides which types need a key: the type alone is not enough.
  assert.equal(missingKey(provider({ type: "anthropic" })), false);
  assert.deepEqual(providerStatus(hosted, { ok: null, checked_ms: null, error: sentence }), { tone: "none", text: "not set up", error: sentence });
  // A result from before the key was removed is not shown as the reason.
  assert.deepEqual(providerStatus(hosted, { ok: false, checked_ms: 1, error: "It refused the key." }), { tone: "none", text: "not set up", error: sentence });
  assert.equal(providerStatus({ ...hosted, has_key: true }, NEVER).text, "untested");
});
