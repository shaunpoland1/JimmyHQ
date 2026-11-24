import assert from "node:assert/strict";
import { test } from "node:test";
import type { ChatOptions, MemoryUsed } from "../src/modules/chat/api.ts";
import { explained, localHelp, memoryAllowed, memoryHelp, memoryNote, newConversationMemory, noteHref } from "../src/modules/chat/lib/memory.ts";

function options(toHosted = false): ChatOptions {
  return {
    roles: [
      { role: "default", provider_id: "den", provider_label: "Den", model: "small", effort: null, local: true, available: true, inherits_default: false, fallbacks: 1, memory: true },
      { role: "smart", provider_id: "far", provider_label: "Far", model: "large", effort: null, local: false, available: true, inherits_default: false, fallbacks: 0, memory: toHosted },
    ],
    providers: [
      { id: "den", label: "Den", type: "openai_compatible", local: true, models: ["small"], listed: true, memory: true },
      { id: "far", label: "Far", type: "openai_compatible", local: false, models: ["large"], listed: true, memory: toHosted },
    ],
    efforts: [],
    defaults: { target: { kind: "role", role: "default" }, effort: null, local_only: false, on_record: true, use_memory: true },
    memory: { to_hosted: toHosted },
    max_message_chars: 1000,
    model_titles: false,
    web: { enabled: false, default: false, search: false, help: "" },
  };
}

function used(over: Partial<MemoryUsed> = {}): MemoryUsed {
  return {
    used: true,
    reason: null,
    receipt_id: "01JABCDEFGHJKMNPQRSTVWXYZ3",
    passages: [{ id: "01JABCDEFGHJKMNPQRSTVWXYZ4", note: "shared/about-me.md", fact_id: "01JABCDEFGHJKMNPQRSTVWXYZ4", chars: 41 }],
    trimmed: 0,
    methods: ["keyword"],
    ...over,
  };
}

test("memory may be sent to a local model and not to a hosted one unless the owner allows it", () => {
  assert.equal(memoryAllowed(options(), { kind: "role", role: "default" }, false), true);
  assert.equal(memoryAllowed(options(), { kind: "role", role: "smart" }, false), false);
  assert.equal(memoryAllowed(options(), { kind: "model", provider_id: "far", model: "large" }, false), false);
  assert.equal(memoryAllowed(options(), { kind: "model", provider_id: "den", model: "small" }, false), true);
  assert.equal(memoryAllowed(options(true), { kind: "role", role: "smart" }, false), true);
});

test("a local only conversation may always be given memory, whatever is chosen", () => {
  assert.equal(memoryAllowed(options(), { kind: "role", role: "smart" }, true), true);
  assert.equal(memoryAllowed(options(), null, true), true);
});

test("nothing is claimed about memory while the choices are unknown", () => {
  assert.equal(memoryAllowed(undefined, { kind: "role", role: "default" }, false), null);
  assert.equal(memoryAllowed(options(), null, false), null);
  assert.equal(memoryAllowed(options(), { kind: "role", role: "code" }, false), null);
  assert.equal(memoryAllowed(options(), { kind: "model", provider_id: "gone", model: "x" }, false), null);
});

test("the memory switch explains itself in one line for each state", () => {
  assert.match(memoryHelp(false, true), /is off/);
  assert.match(memoryHelp(true, true), /is on: before each reply/);
  assert.match(memoryHelp(true, null), /is on: before each reply/);
  const blocked = memoryHelp(true, false);
  assert.match(blocked, /hosted/);
  assert.match(blocked, /Settings, Chat/);
  assert.notEqual(localHelp(true), localHelp(false));
});

test("only one switch is explained at a time: pointed at, then focused, then just changed", () => {
  assert.equal(explained(null, null, null), null);
  assert.equal(explained(null, null, "memory"), "memory");
  assert.equal(explained(null, "record", "memory"), "record");
  assert.equal(explained("local", "record", "memory"), "local");
});

test("a new conversation is sent the memory choice only if the owner made one", () => {
  assert.deepEqual(newConversationMemory(null), {});
  assert.deepEqual(newConversationMemory(false), { use_memory: false });
  assert.deepEqual(newConversationMemory(true), { use_memory: true });
});

test("a reply written with memory off says nothing about memory", () => {
  assert.equal(memoryNote(null), null);
  assert.equal(memoryNote(undefined), null);
});

test("a reply that used memory says how many, and what was left out for space", () => {
  assert.deepEqual(memoryNote(used()), { kind: "used", label: "Used 1 memory", detail: null });
  const two = used({ passages: [...used().passages, { id: "c:0123456789abcdef", note: "shared/travel.md", fact_id: null, chars: 30 }], trimmed: 1 });
  assert.deepEqual(memoryNote(two), { kind: "used", label: "Used 2 memories", detail: "1 more was found and left out for space." });
  assert.match(memoryNote(used({ trimmed: 3 }))?.kind === "used" ? (memoryNote(used({ trimmed: 3 })) as { detail: string }).detail : "", /3 more were/);
});

test("a reply that had memory on and did not use it gives the reason", () => {
  const reason = "Memory was searched and nothing in it bore on this message.";
  assert.deepEqual(memoryNote(used({ used: false, reason, passages: [] })), { kind: "skipped", reason });
  const bare = memoryNote(used({ used: false, reason: null, passages: [] }));
  assert.equal(bare?.kind, "skipped");
});

test("a note's link opens it on the Memory screen, with each part of its path encoded", () => {
  assert.equal(noteHref("shared/about-me.md"), "#/memory/shared/about-me.md");
  assert.equal(noteHref("agents/scout/to do.md"), "#/memory/agents/scout/to%20do.md");
});

test("with no model set up, memory is not blamed on a hosted model", () => {
  const empty: ChatOptions = { ...options(), providers: [], roles: options().roles.map((role) => ({ ...role, provider_id: null, provider_label: null, model: null, available: false, local: null, memory: false })) };
  assert.equal(memoryAllowed(empty, { kind: "role", role: "default" }, false), null);
  assert.equal(memoryAllowed(empty, { kind: "role", role: "smart" }, false), null);
  assert.ok(!memoryHelp(true, memoryAllowed(empty, { kind: "role", role: "default" }, false)).includes("hosted"));
});
