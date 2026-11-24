import assert from "node:assert/strict";
import { test } from "node:test";
import type { RemoteStatus } from "../src/modules/remote/api.ts";
import { actions, affectsRemoteStatus, headline, steps, turnOffPlan, turnOnPlan, turnedOffLine, turnedOnLine } from "../src/modules/remote/lib/status.ts";

const NAME = "example-machine.example-tailnet.ts.net";

function status(changes: Partial<RemoteStatus> = {}): RemoteStatus {
  return {
    state: "ready", problem: null, path_missing: false, backend_state: "Running", name: NAME, https_enabled: true,
    port: 8440, serve_known: true, mapped: false, address: null, public: false, clash: false, allowed: false, from_this_computer: true,
    commands: { on: "tailscale serve --bg --https=8440 http://127.0.0.1:8440", off: "tailscale serve --https=8440 off", status: "tailscale serve status", allowed_hosts_value: NAME },
    ...changes,
  };
}

test("each way Tailscale can be unusable gets its own plain label and keeps the server's sentence", () => {
  const labels = new Set<string>();
  for (const state of ["not_installed", "not_running", "needs_login", "stopped", "unknown"] as const) {
    const said = headline(status({ state, name: null, problem: "Do this." }));
    assert.equal(said.tone, "off");
    assert.equal(said.detail, "Do this.");
    labels.add(said.label);
  }
  assert.equal(labels.size, 5);
});

test("a ready Tailscale that is not mapped yet says it is ready to turn on", () => {
  assert.deepEqual(headline(status()).label, "Ready to turn on");
  assert.equal(headline(status()).tone, "off");
});

test("a mapping that JimmyHQ answers to is reachable, and one it does not answer to says what is missing", () => {
  const reachable = headline(status({ mapped: true, allowed: true, address: `https://${NAME}:8440` }));
  assert.equal(reachable.tone, "ok");
  const missing = headline(status({ mapped: true, allowed: false, address: `https://${NAME}:8440` }));
  assert.equal(missing.tone, "warn");
  assert.match(missing.detail, /allowed hosts/);
});

test("a mapping open to the public internet is a warning even when it is allowed", () => {
  const open = headline(status({ mapped: true, allowed: true, public: true }));
  assert.equal(open.tone, "warn");
  assert.match(open.label, /public internet/);
});

test("an address already used for something else is a warning, and a missing name is explained", () => {
  assert.match(headline(status({ clash: true })).detail, /port 8440/);
  const unnamed = headline(status({ name: null, problem: "Turn on MagicDNS." }));
  assert.equal(unnamed.detail, "Turn on MagicDNS.");
});

test("at the computer itself a ready Tailscale offers Turn on, and a mapped one offers Turn off", () => {
  assert.deepEqual(actions(status()), { turnOn: true, turnOff: false, allowHost: true, atMachine: false });
  assert.deepEqual(actions(status({ mapped: true, allowed: true })), { turnOn: false, turnOff: true, allowHost: false, atMachine: false });
});

test("from another device the buttons that change the computer are replaced by the at-the-machine sentence", () => {
  assert.deepEqual(actions(status({ from_this_computer: false })), { turnOn: false, turnOff: false, allowHost: true, atMachine: true });
  assert.deepEqual(actions(status({ from_this_computer: false, mapped: true, allowed: true })), { turnOn: false, turnOff: false, allowHost: false, atMachine: true });
});

test("adding the name to the allowed hosts stays available from anywhere, and only while it is missing", () => {
  assert.equal(actions(status({ from_this_computer: false, mapped: true })).allowHost, true);
  assert.equal(actions(status({ from_this_computer: false, mapped: true, allowed: true })).allowHost, false);
});

test("adding the name is never offered while Funnel makes the address public", () => {
  assert.equal(actions(status({ mapped: true, public: true })).allowHost, false);
  assert.equal(actions(status({ mapped: true, public: true, from_this_computer: false })).allowHost, false);
  assert.equal(actions(status({ mapped: true, public: false })).allowHost, true);
});

test("the public warning gives the command that turns Funnel off for this port", () => {
  assert.match(headline(status({ mapped: true, public: true })).detail, /tailscale funnel --https=8440 off/);
  const on443 = status({ mapped: true, public: true, commands: { ...status().commands, off: "tailscale serve --https=443 off" } });
  assert.match(headline(on443).detail, /tailscale funnel --https=443 off/);
});

test("nothing is offered, and no at-the-machine sentence, while Tailscale cannot be used", () => {
  for (const state of ["not_installed", "not_running", "needs_login", "stopped", "unknown"] as const) {
    assert.deepEqual(actions(status({ state, name: null, from_this_computer: false })), { turnOn: false, turnOff: false, allowHost: false, atMachine: false });
  }
  assert.equal(actions(status({ name: null })).turnOn, false);
});

test("Turn on is not offered over something else on the port or when the port is unknown", () => {
  assert.equal(actions(status({ clash: true })).turnOn, false);
  assert.equal(actions(status({ port: 0 })).turnOn, false);
  assert.equal(actions(status({ clash: true, from_this_computer: false })).atMachine, false);
});

test("the confirmation shows the exact command, the name that gets allowed, and that it stays private", () => {
  const plan = turnOnPlan(status());
  assert.equal(plan.command, "tailscale serve --bg --https=8440 http://127.0.0.1:8440");
  assert.ok(plan.changes.some((line) => line.includes(NAME) && line.includes("Other names for this server")));
  assert.ok(plan.changes.some((line) => /not opened to the public internet/.test(line)));
  assert.ok(turnOnPlan(status({ allowed: true })).changes.some((line) => /already in the allowed hosts/.test(line)));
  assert.equal(turnOffPlan(status({ mapped: true })).command, "tailscale serve --https=8440 off");
});

test("the result lines say what happened and where to open JimmyHQ", () => {
  const done = status({ mapped: true, address: `https://${NAME}:8440` });
  assert.match(turnedOnLine({ ran: "x", allowed_added: true, status: done }), /forwarding to JimmyHQ.*Open https:\/\/example-machine/);
  assert.match(turnedOnLine({ ran: null, allowed_added: false, status: done }), /already on/);
  assert.match(turnedOffLine({ ran: "x", host_removed: true, status: done }), /removed from the allowed hosts/);
  assert.match(turnedOffLine({ ran: null, host_removed: false, status: done }), /already off/);
});

test("the do-it-yourself steps are three, with the real command and name when they are known", () => {
  const list = steps(status());
  assert.equal(list.length, 3);
  assert.equal(list[0]?.command, "tailscale serve --bg --https=8440 http://127.0.0.1:8440");
  assert.ok(list[1]?.text.includes(NAME));
  assert.ok(list[2]?.text.includes(`https://${NAME}:8440`));
});

test("the steps still read well when Tailscale is not available to give a name or a command", () => {
  const list = steps(status({ state: "not_installed", name: null, port: 8440, commands: { on: null, off: null, status: "tailscale serve status", allowed_hosts_value: null } }));
  assert.equal(list.length, 3);
  assert.match(list[0]?.command ?? "", /^tailscale serve --bg/);
  assert.match(list[1]?.text ?? "", /<machine-name>/);
});

test("changes to the allowed hosts, the Tailscale path or a remote receipt make the status worth asking for again", () => {
  assert.equal(affectsRemoteStatus({ kind: "remote.serve_changed", payload: {} }), true);
  assert.equal(affectsRemoteStatus({ kind: "setting.updated", payload: { key: "access.allowed_hosts" } }), true);
  assert.equal(affectsRemoteStatus({ kind: "setting.reset", payload: { key: "remote.tailscale_path" } }), true);
  assert.equal(affectsRemoteStatus({ kind: "setting.updated", payload: { key: "access.session_days" } }), false);
  assert.equal(affectsRemoteStatus({ kind: "chat.message.sent" }), false);
});
