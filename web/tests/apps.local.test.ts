// Addresses on the owner's own network he lets one app reach: the page's side, and the
// shell's early check on a page's net.fetch. The server holds every rule again.

import assert from "node:assert/strict";
import { test } from "node:test";
import { originOf, readMount, refusalFor } from "../src/modules/apps/lib/bridge.ts";
import {
  addWarning,
  leadsLine,
  localCheckPath,
  localConfirmPath,
  localKeyPath,
  localPath,
  localProblem,
  moved,
  movedQuestion,
  readCheck,
  readLocal,
  removeLocalQuestion,
} from "../src/modules/apps/lib/local.ts";

const ORIGIN = "http://192.0.2.10:8000";
const ADDRESS_ID = "0123456789abcdef";

test("the routes are built only for an app id and a key of the right shape", () => {
  assert.equal(localPath("imagery"), "/api/apps/imagery/local-addresses");
  assert.equal(localKeyPath("imagery", ADDRESS_ID), `/api/apps/imagery/local-addresses/${ADDRESS_ID}`);
  assert.equal(localCheckPath("imagery"), "/api/apps/imagery/local-addresses/check");
  assert.equal(localConfirmPath("imagery", ADDRESS_ID), `/api/apps/imagery/local-addresses/${ADDRESS_ID}/confirm`);
  assert.equal(localConfirmPath("imagery", "../x"), null);
  for (const [app, key] of [["../x", ADDRESS_ID], ["imagery", "../../hosts"], ["imagery", ORIGIN], ["Imagery", ADDRESS_ID]]) {
    assert.equal(localKeyPath(app as string, key as string), null);
  }
});

test("only well-formed addresses from the server are shown", () => {
  const read = readLocal({ max: 3, addresses: [
    { address: ORIGIN, key: ADDRESS_ID, added_at: 5, resolved: ["192.0.2.10", "<b>", 7] },
    { address: "javascript:alert(1)", key: ADDRESS_ID },
    { address: `${ORIGIN}/path`, key: ADDRESS_ID },
    { address: ORIGIN, key: "nope" },
  ] });
  assert.deepEqual(read, { max: 3, addresses: [{ address: ORIGIN, key: ADDRESS_ID, addedAt: 5, resolved: ["192.0.2.10"] }] });
  assert.deepEqual(readLocal(null), { max: 0, addresses: [] });
});

test("what the owner typed is checked before anything is sent", () => {
  const none = { max: 1, addresses: [] };
  assert.equal(localProblem(ORIGIN, none), null);
  assert.match(localProblem("", none) ?? "", /Nothing was added/);
  assert.match(localProblem("192.0.2.10:8000", none) ?? "", /http:\/\/ or https:\/\//);
  assert.match(localProblem("http://*.example.com:80", none) ?? "", /one exact address/);
  assert.match(localProblem(ORIGIN, { max: 1, addresses: [{ address: ORIGIN, key: ADDRESS_ID, addedAt: 0, resolved: [] }] }) ?? "", /at most 1/);
});

test("adding warns plainly that the app will reach that one address on the owner's network", () => {
  const warning = addWarning("Imagery", { address: ORIGIN, resolved: ["192.0.2.10"] });
  assert.equal(warning.title, `Let Imagery reach ${ORIGIN}?`);
  assert.match(warning.message, /exactly that address and port, and nothing else there/);
  assert.doesNotMatch(warning.message, /leads to/); // an address typed as one leads only there
  assert.match(warning.message, /192\.0\.2\.10 is an address, not a name, so it is used exactly as you typed it/);
  assert.match(removeLocalQuestion("Imagery", ORIGIN).message, /no longer be able to reach/);
});

test("an origin is a scheme, a host and a port, with the scheme's own port written out", () => {
  assert.equal(originOf("http://192.0.2.10:8000/v1/images?x=1"), ORIGIN);
  assert.equal(originOf("HTTP://Example-Machine.example.com/"), "http://example-machine.example.com:80");
  assert.equal(originOf("https://192.0.2.10/"), "https://192.0.2.10:443");
  assert.equal(originOf("http://[::1]:8000/"), "http://[::1]:8000");
  for (const odd of ["ftp://192.0.2.10:21/", "http://user:pw@192.0.2.10:8000/", "nope", 5, null]) assert.equal(originOf(odd), null);
});

test("a page reaches an address on the owner's network only when he added exactly it", () => {
  const mounted = readMount({ mount: "m".repeat(32), entry: "index.html", version: "1.0.0", tier: "full", permissions: [], local_addresses: [ORIGIN, "javascript:x", 5] });
  assert.deepEqual(mounted?.grant, { tier: "full", permissions: [], ownNetwork: [ORIGIN] });
  const grant = mounted?.grant ?? { tier: "full" as const, permissions: [] };
  assert.equal(refusalFor("net.fetch", { url: `${ORIGIN}/v1/images` }, grant), null);
  for (const url of ["http://192.0.2.10:8001/", "https://192.0.2.10:8000/", "http://192.0.2.11:8000/", "http://192.0.2.10/"]) {
    assert.equal(refusalFor("net.fetch", { url }, grant)?.code, "not_allowed", url);
  }
  // With none added, the grant reads as it always did.
  assert.deepEqual(readMount({ mount: "m".repeat(32), entry: "index.html", version: "1.0.0", tier: "full", permissions: [] })?.grant, { tier: "full", permissions: [] });
});

const NAMED = "http://images.example.net:8000";

test("for a name, the warning shows where it leads now and says a later move stops the app", () => {
  const warning = addWarning("Imagery", { address: NAMED, resolved: ["192.0.2.20", "2001:db8::20"] });
  assert.match(warning.message, /images\.example\.net leads to 192\.0\.2\.20, 2001:db8::20 just now/);
  assert.match(warning.message, /stopped from reaching it until you agree to the new address/);
});

test("only a well-formed answer to a check is offered to agree to", () => {
  const good = { address: NAMED, key: ADDRESS_ID, resolved: ["192.0.2.20"], warning: "x", added: false, stored: null };
  assert.deepEqual(readCheck(good), { address: NAMED, key: ADDRESS_ID, resolved: ["192.0.2.20"], added: false, stored: null });
  for (const bad of [null, { ...good, resolved: [] }, { ...good, resolved: ["<script>"] }, { ...good, key: "../x" }, { ...good, address: `${NAMED}/x` }]) {
    assert.equal(readCheck(bad), null);
  }
});

test("a name that leads somewhere else now asks the owner before the app reaches it again", () => {
  const now = readCheck({ address: NAMED, key: ADDRESS_ID, resolved: ["192.0.2.30"], added: true, stored: ["192.0.2.20"] });
  assert.ok(now && moved(now));
  const question = movedQuestion("Imagery", now!);
  assert.match(question.message, /led to 192\.0\.2\.20/);
  assert.match(question.message, /now leads to 192\.0\.2\.30/);
  assert.match(question.message, /cannot reach .* until you agree/);
  const same = readCheck({ address: NAMED, key: ADDRESS_ID, resolved: ["192.0.2.20"], added: true, stored: ["192.0.2.20"] });
  assert.ok(same && !moved(same));
  assert.equal(leadsLine({ address: NAMED, key: ADDRESS_ID, addedAt: 0, resolved: ["192.0.2.20"] }), "Leads to 192.0.2.20.");
  assert.match(leadsLine({ address: NAMED, key: ADDRESS_ID, addedAt: 0, resolved: [] }) ?? "", /Check it/);
  assert.equal(leadsLine({ address: ORIGIN, key: ADDRESS_ID, addedAt: 0, resolved: ["192.0.2.10"] }), null);
});
