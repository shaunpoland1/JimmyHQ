// The Packages page: the Add a package form and its carrier picker.

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync, readdirSync } from "node:fs";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { DETECT, OTHER, addProblem, carrierFrom, carrierOptions, choiceOf, cleanNumber } from "../../apps/packages/web/lib/form.js";

const saved = [{ code: "3011", name: "Corner Post" }];

test("a tracking number is cleaned of spaces and nothing else", () => {
  assert.equal(cleanNumber("  ZX1002 00300 QQ "), "ZX100200300QQ");
  assert.equal(cleanNumber(null), "");
});

test("the form says what is missing before it sends anything", () => {
  assert.match(addProblem({ label: " ", number: "ZX100200300QQ" }), /short name/);
  assert.match(addProblem({ label: "Garden hose", number: "  " }), /Type the tracking number/);
  assert.equal(addProblem({ label: "Garden hose", number: "ZX100200300QQ" }), "");
});

test("the picker offers detecting first, then the saved carriers, then another code", () => {
  const options = carrierOptions(saved, { registers: true });
  assert.deepEqual(options.map((o: { value: string }) => o.value), [DETECT, "3011", OTHER]);
  assert.equal(options[0].label, "Detect it");
  assert.equal(options[1].label, "Corner Post (3011)");
  assert.equal(carrierOptions([], { registers: false })[0].label, "Any (none chosen)");
});

test("choosing a saved carrier sends its code, and typing another sends the code and its name", () => {
  assert.deepEqual(carrierFrom("3011", "", ""), { carrier: "3011", carrier_name: "" });
  assert.deepEqual(carrierFrom(DETECT, "9", "ignored"), { carrier: "", carrier_name: "" });
  assert.deepEqual(carrierFrom(OTHER, " 30 21 ", "  Red Van "), { carrier: "3021", carrier_name: "Red Van" });
});

test("the picker starts on the carrier a package already has", () => {
  assert.equal(choiceOf({ carrier: "" }, saved), DETECT);
  assert.equal(choiceOf({ carrier: "3011" }, saved), "3011");
  assert.equal(choiceOf({ carrier: "4044" }, saved), OTHER);
});

test("the page never uses the browser's own dialogs and builds its text as text", () => {
  const folder = new URL("../../apps/packages/web/", import.meta.url);
  const files = ["app.js", "views/form.js", "views/list.js", ...readdirSync(new URL("lib/", folder)).map((f) => `lib/${f}`)];
  for (const file of files) {
    const text = readFileSync(new URL(file, folder), "utf8");
    assert.doesNotMatch(text, /\b(alert|confirm|prompt)\s*\(/, file);
    assert.doesNotMatch(text, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/, file);
  }
});
