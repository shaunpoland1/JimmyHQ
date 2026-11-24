import assert from "node:assert/strict";
import { test } from "node:test";
import type { NodeRecord } from "../src/modules/nodes/api.ts";
import { addressNote, affectsNodes, ago, codeLife, joinCommand, listed, nodeStatus, offersLine, onlyWorksHere, removeMessage, removedLine, rolesUsing, sortNodes } from "../src/modules/nodes/lib/nodes.ts";

const NOW = 1_800_000_000_000;

function node(changes: Partial<NodeRecord> = {}): NodeRecord {
  return {
    id: "01JEXAMPLE0000000000000000", name: "example-box", joined_ms: NOW - 86_400_000, version: "0.1.0", offers: ["models"],
    models: [{ name: "big", provider_id: "n-example-box_big", roles: ["default"] }], browsers: [], connected: true, last_seen_ms: NOW - 4_000,
    skew: null, blocked: false,
    ...changes,
  };
}

test("a node is connected, out of reach, not connected yet, or on a version that cannot be worked with", () => {
  assert.deepEqual(nodeStatus(node(), NOW), { tone: "ok", label: "Connected", detail: null });
  const away = nodeStatus(node({ connected: false, last_seen_ms: NOW - 5 * 60_000 }), NOW);
  assert.equal(away.tone, "warn");
  assert.equal(away.label, "Out of reach");
  assert.match(away.detail ?? "", /^Last heard from 5 minutes ago\./);
  assert.match(away.detail ?? "", /passed over until it is back/);
  const fresh = nodeStatus(node({ connected: false, last_seen_ms: null, version: null }), NOW);
  assert.equal(fresh.label, "Not connected yet");
  assert.match(fresh.detail ?? "", /jimmyhq node run/);
  const blocked = nodeStatus(node({ blocked: true, skew: "The two cannot work together." }), NOW);
  assert.deepEqual(blocked, { tone: "warn", label: "Versions do not match", detail: "The two cannot work together." });
});

test("a version difference that still works is said under a connected node", () => {
  const said = nodeStatus(node({ skew: "They still work together." }), NOW);
  assert.equal(said.tone, "ok");
  assert.equal(said.detail, "They still work together.");
});

test("how long ago is said in words", () => {
  assert.equal(ago(NOW - 10_000, NOW), "just now");
  assert.equal(ago(NOW - 60_000, NOW), "1 minute ago");
  assert.equal(ago(NOW - 3 * 3_600_000, NOW), "3 hours ago");
  assert.equal(ago(NOW - 5 * 86_400_000, NOW), "5 days ago");
  assert.equal(ago(NOW + 5_000, NOW), "just now");
});

test("what a node offers is one line, and a node that offers nothing says how to offer", () => {
  assert.equal(offersLine(node({ models: [{ name: "big", provider_id: "a", roles: [] }, { name: "small", provider_id: "b", roles: [] }] })), "Models: big, small");
  assert.equal(offersLine(node({ browsers: ["main"] })), "Models: big. Browsers: main");
  assert.match(offersLine(node({ models: [] })), /^Offers nothing yet\. .*jimmyhq node offer model/);
});

test("the confirm dialog names every role that will lose its model", () => {
  const two = node({ models: [{ name: "big", provider_id: "a", roles: ["smart", "default"] }, { name: "small", provider_id: "b", roles: ["default", "cheap"] }] });
  assert.deepEqual(rolesUsing(two), ["cheap", "default", "smart"]);
  assert.equal(
    removeMessage(two),
    "example-box is cut off at once and its token stops working. The cheap, default and smart roles use a model on it and lose it. Each moves to its next choice, or has no model if it has no other.",
  );
  assert.equal(
    removeMessage(node()),
    "example-box is cut off at once and its token stops working. The default role uses a model on it and loses it. It moves to its next choice, or has no model if it has no other.",
  );
  assert.match(removeMessage(node({ models: [] })), /No role uses a model on it, so no role changes\.$/);
});

test("after a removal the line says which roles were changed", () => {
  assert.equal(removedLine({ ok: true, name: "example-box", roles_changed: [] }), "example-box was removed.");
  assert.equal(removedLine({ ok: true, name: "example-box", roles_changed: ["default"] }), "example-box was removed. The default role was changed: check it under Settings, Models.");
  assert.equal(removedLine({ ok: true, name: "example-box", roles_changed: ["cheap", "default"] }), "example-box was removed. The cheap and default roles were changed: check them under Settings, Models.");
});

test("the join command carries the address the owner is using and the code", () => {
  assert.equal(joinCommand("https://example-machine.example-tailnet.ts.net:8440", "ABCDE-FGHJK"), "jimmyhq node join https://example-machine.example-tailnet.ts.net:8440 ABCDE-FGHJK");
  assert.equal(addressNote("https://example-machine.example-tailnet.ts.net:8440"), null);
  assert.equal(joinCommand("http://192.0.2.10:8440", "ABCDE-FGHJK"), "jimmyhq node join http://192.0.2.10:8440 ABCDE-FGHJK");
  assert.match(addressNote("http://192.0.2.10:8440") ?? "", /plain http.*--allow-plain-http/);
});

test("an address that only works on this computer is never put in the command as it is", () => {
  for (const origin of ["http://127.0.0.1:8440", "http://localhost:8440", "http://[::1]:8440"]) {
    assert.equal(onlyWorksHere(origin), true);
    assert.equal(joinCommand(origin, "ABCDE-FGHJK"), "jimmyhq node join http://ADDRESS-OF-THIS-COMPUTER:8440 ABCDE-FGHJK");
    assert.match(addressNote(origin) ?? "", /replace ADDRESS-OF-THIS-COMPUTER/);
  }
  assert.equal(onlyWorksHere("http://192.0.2.10:8440"), false);
  assert.equal(onlyWorksHere("not a url"), true);
});

test("a join code says how long it still works, then that it has run out", () => {
  assert.deepEqual(codeLife(NOW + 9.5 * 60_000, NOW), { live: true, text: "Works once, for 10 more minutes. It is not shown again." });
  assert.deepEqual(codeLife(NOW + 30_000, NOW), { live: true, text: "Works once, for 1 more minute. It is not shown again." });
  assert.deepEqual(codeLife(NOW, NOW), { live: false, text: "This code has run out. Make a new one." });
});

test("nodes are listed oldest first and lists read as a sentence", () => {
  const sorted = sortNodes([node({ id: "b", name: "b", joined_ms: 2 }), node({ id: "a", name: "a", joined_ms: 1 }), node({ id: "c", name: "a2", joined_ms: 2 })]);
  assert.deepEqual(sorted.map((n) => n.id), ["a", "c", "b"]);
  assert.equal(listed([]), "");
  assert.equal(listed(["a"]), "a");
  assert.equal(listed(["a", "b"]), "a and b");
  assert.equal(listed(["a", "b", "c"]), "a, b and c");
});

test("only node events and role changes refresh the screen", () => {
  assert.equal(affectsNodes({ kind: "node.joined" }), true);
  assert.equal(affectsNodes({ kind: "node.offered" }), true);
  assert.equal(affectsNodes({ kind: "node.left" }), true);
  assert.equal(affectsNodes({ kind: "model.role.set" }), true);
  assert.equal(affectsNodes({ kind: "model.called" }), false);
  assert.equal(affectsNodes({ kind: "task.created" }), false);
});
