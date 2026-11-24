import assert from "node:assert/strict";
import { test } from "node:test";
import { addressLine, connectShape, inboxNameProblem, makesInboxes } from "../src/modules/hands/lib/mail.ts";

const address = ["ada", "example.com"].join("@");

test("an AgentMail account is always an agent's own inbox and asks for its name in sight", () => {
  const shape = connectShape("agentmail");
  assert.equal(shape.ownInboxFixed, true);
  assert.equal(shape.appInSight, true);
  assert.match(shape.appLabel, /Inbox name/);
  assert.ok(shape.intro && /new inbox with its own address/.test(shape.intro));
});

test("other providers keep the app field under Advanced and leave the inbox box to the owner", () => {
  for (const type of ["composio", "mcp", undefined, null]) {
    const shape = connectShape(type);
    assert.equal(shape.ownInboxFixed, false);
    assert.equal(shape.appInSight, false);
    assert.equal(shape.intro, null);
  }
  assert.equal(makesInboxes("composio"), false);
});

test("an inbox name follows the server's rule, and empty lets the provider choose", () => {
  assert.equal(inboxNameProblem(""), null);
  assert.equal(inboxNameProblem("ada.helper-1"), null);
  assert.equal(inboxNameProblem("Ada"), null); // sent in lower case
  assert.ok(inboxNameProblem("ada lovelace"));
  assert.ok(inboxNameProblem("-ada"));
  assert.ok(inboxNameProblem("a".repeat(65)));
});

test("an account with an address of its own shows it, and one without shows nothing", () => {
  assert.equal(addressLine({ address, own_inbox: true }), `Agent's address: ${address}`);
  assert.equal(addressLine({ address, own_inbox: false }), `Address: ${address}`);
  assert.equal(addressLine({ address: null, own_inbox: true }), null);
  assert.equal(addressLine({ address: "  ", own_inbox: true }), null);
});
