import assert from "node:assert/strict";
import { test } from "node:test";
import type { Provider, RoleEntry } from "../src/modules/models/api.ts";
import { checkRole, draftRole, emptyTarget, moveItem, removeAt, replaceAt, roleList, roleUse, roleWarnings, rolesUsing, targetError, viewTarget } from "../src/modules/models/lib/roles.ts";

function provider(id: string, patch: Partial<Provider> = {}): Provider {
  return {
    id,
    type: "openai_compatible",
    label: id.toUpperCase(),
    base_url: "http://127.0.0.1:8080/v1",
    enabled: true,
    local: true,
    options: { max_tokens: null, temperature: null, effort: null, native_json: null },
    region: null,
    profile: null,
    has_key: false,
    needs_key: false,
    health: { ok: null, checked_ms: null, error: null },
    ...patch,
  };
}

const providers = [provider("den"), provider("cloud", { local: false }), provider("off", { enabled: false })];
const target = (provider_id: string, model = "m") => ({ provider_id, model, effort: null });

function entry(role: string, primary: string | null, fallbacks: string[] = []): RoleEntry {
  return { role, primary: primary ? target(primary) : null, fallbacks: fallbacks.map((id) => target(id)), inherits_default: false };
}

test("roles pointing at a provider are found through first choices and fallbacks", () => {
  const roles = [entry("default", "den"), entry("cheap", "cloud", ["den"]), entry("smart", "cloud"), entry("code", null)];
  assert.deepEqual(rolesUsing(roles, "den"), ["default", "cheap"]);
  assert.deepEqual(rolesUsing(roles, "nobody"), []);
});

test("a list of roles reads as a phrase", () => {
  assert.equal(roleList(["default"]), "the default role");
  assert.equal(roleList(["default", "code"]), "the default and code roles");
  assert.equal(roleList(["default", "code", "smart"]), "the default, code and smart roles");
});

test("known roles say what they are used for and unknown ones say nothing", () => {
  assert.ok(roleUse("default"));
  assert.equal(roleUse("something-new"), "");
});

test("a target is described by its provider's label, where it runs and its state", () => {
  assert.deepEqual(viewTarget(target("den", "small"), providers), { providerLabel: "DEN", model: "small", effort: null, isLocal: true, state: "ok" });
  assert.equal(viewTarget(target("off"), providers).state, "off");
  assert.deepEqual(viewTarget(target("gone"), providers), { providerLabel: "gone", model: "m", effort: null, isLocal: null, state: "missing" });
});

test("an unset role and a local first choice have no warnings", () => {
  assert.deepEqual(roleWarnings(entry("code", null), providers), []);
  assert.deepEqual(roleWarnings(entry("default", "den"), providers), []);
});

test("a hosted first choice with no local fallback is warned about", () => {
  assert.equal(roleWarnings(entry("smart", "cloud"), providers).length, 1);
  assert.match(roleWarnings(entry("smart", "cloud"), providers)[0] ?? "", /no local fallback/);
  assert.deepEqual(roleWarnings(entry("smart", "cloud", ["den"]), providers), []);
});

test("a switched-off local fallback does not count as a local fallback", () => {
  const warnings = roleWarnings(entry("smart", "cloud", ["off"]), providers);
  assert.equal(warnings.length, 2);
  assert.ok(warnings.some((warning) => /switched off/.test(warning)));
  assert.ok(warnings.some((warning) => /no local fallback/.test(warning)));
});

test("a role pointing at a switched-off or removed provider is warned about", () => {
  assert.match(roleWarnings(entry("default", "off"), providers)[0] ?? "", /switched off/);
  assert.match(roleWarnings(entry("default", "gone"), providers)[0] ?? "", /no longer exists/);
  assert.match(roleWarnings(entry("default", "den", ["gone"]), providers)[0] ?? "", /fallback points at a provider that no longer exists/);
});

test("a target needs a provider and a model", () => {
  assert.ok(targetError(emptyTarget()));
  assert.ok(targetError({ provider_id: "den", model: "  ", effort: "" }));
  assert.ok(targetError({ provider_id: "den", model: "x".repeat(201), effort: "" }));
  assert.equal(targetError({ provider_id: "den", model: "small", effort: "low" }), null);
});

test("a cleared role saves as no first choice and no fallbacks", () => {
  assert.deepEqual(checkRole({ primary: null, fallbacks: [] }), { ok: true, body: { primary: null, fallbacks: [] } });
  assert.equal(checkRole({ primary: null, fallbacks: [emptyTarget("den")] }).ok, false);
});

test("a complete role saves trimmed models with unset efforts as null", () => {
  const draft = draftRole({ primary: { provider_id: "cloud", model: "big", effort: "high" }, fallbacks: [target("den", "small")] });
  draft.fallbacks[0] = { provider_id: "den", model: " small ", effort: "" };
  assert.deepEqual(checkRole(draft), {
    ok: true,
    body: { primary: { provider_id: "cloud", model: "big", effort: "high" }, fallbacks: [{ provider_id: "den", model: "small", effort: null }] },
  });
});

test("an unfinished fallback is reported on its own line", () => {
  const result = checkRole({ primary: { provider_id: "den", model: "small", effort: "" }, fallbacks: [emptyTarget("den")] });
  assert.equal(result.ok, false);
  if (!result.ok) {
    assert.equal(result.primary, null);
    assert.ok(result.fallbacks[0]);
  }
});

test("more than eight fallbacks cannot be saved", () => {
  const fallbacks = Array.from({ length: 9 }, () => ({ provider_id: "den", model: "small", effort: "" as const }));
  assert.equal(checkRole({ primary: { provider_id: "den", model: "small", effort: "" }, fallbacks }).ok, false);
});

test("list helpers move, remove and replace without changing the original", () => {
  const items = ["a", "b", "c"];
  assert.deepEqual(moveItem(items, 0, 1), ["b", "a", "c"]);
  assert.deepEqual(moveItem(items, 2, -1), ["a", "c", "b"]);
  assert.deepEqual(moveItem(items, 0, -1), ["a", "b", "c"]);
  assert.deepEqual(moveItem(items, 2, 1), ["a", "b", "c"]);
  assert.deepEqual(removeAt(items, 1), ["a", "c"]);
  assert.deepEqual(replaceAt(items, 1, "x"), ["a", "x", "c"]);
  assert.deepEqual(items, ["a", "b", "c"]);
});
