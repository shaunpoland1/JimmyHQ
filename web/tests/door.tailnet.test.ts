import assert from "node:assert/strict";
import { test } from "node:test";
import { tailnetText } from "../src/modules/door/lib/tailnet.ts";

const ADDRESS = "https://hub.example-tailnet.ts.net:8440/mcp";

test("the Tailscale choice names the address and says when the door moves or an address is replaced", () => {
  const offer = tailnetText({ address: ADDRESS, in_use: false, moves_door: true }, true);
  assert.ok(offer.note.includes(ADDRESS) && offer.note.includes("moves") && offer.note.includes("replaces"));
  assert.equal(offer.button, "Use the Tailscale address");
  const plain = tailnetText({ address: ADDRESS, in_use: false, moves_door: false }, false);
  assert.ok(!plain.note.includes("moves") && !plain.note.includes("replaces"));
});

test("once in use, the choice offers the way back to the local address", () => {
  const used = tailnetText({ address: ADDRESS, in_use: true, moves_door: false }, true);
  assert.equal(used.button, "Use the local address");
  assert.ok(used.note.includes("nowhere else"));
});
