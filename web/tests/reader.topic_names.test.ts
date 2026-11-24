// The Reader page: suggested topic names as chips that fill the Name field.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { chipNames, fillName, mayFill } from "../../apps/reader/web/lib/topic_names.js";

test("a chip fills an empty name", () => {
  assert.deepEqual(fillName("  ", null, "Urban beekeeping"), { name: "Urban beekeeping", filled: "Urban beekeeping", changed: true });
});

test("a chip replaces a name an earlier chip filled", () => {
  const got = fillName("Urban beekeeping", "Urban beekeeping", "Home brewing");
  assert.deepEqual(got, { name: "Home brewing", filled: "Home brewing", changed: true });
});

test("a chip never replaces text the owner typed or edited", () => {
  assert.equal(mayFill("My own topic", null), false);
  assert.equal(mayFill("Urban beekeeping!", "Urban beekeeping"), false);
  const got = fillName("My own topic", "Urban beekeeping", "Home brewing");
  assert.deepEqual(got, { name: "My own topic", filled: "Urban beekeeping", changed: false });
});

test("a blank chip changes nothing", () => {
  assert.equal(fillName("", null, "   ").changed, false);
});

test("chips are plain, each once, and never a topic the owner has", () => {
  assert.deepEqual(chipNames(["  Home  brewing", "home brewing", "", null, "Bees", "Birds"], ["bees"]),
    ["Home brewing", "Birds"]);
  assert.deepEqual(chipNames(undefined), []);
});
