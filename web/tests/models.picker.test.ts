import assert from "node:assert/strict";
import { test } from "node:test";
import { buildPickerOptions, describePick, localityWord, moveHighlight, pickerItems, sameTarget, targetKey, typeahead, popupShift } from "../src/modules/models/lib/picker.ts";
import type { ModelPickerOptions } from "../src/modules/models/lib/picker.ts";

const options: ModelPickerOptions = {
  roles: [
    { role: "default", provider_label: "Den", model: "small", local: true },
    { role: "smart", provider_label: "Cloud", model: "big", local: false },
    { role: "embeddings", provider_label: null, model: null, local: null },
  ],
  providers: [
    { id: "den", label: "Den", local: true, models: ["small", "tiny"] },
    { id: "cloud", label: "Cloud", local: false, models: ["big"] },
  ],
  efforts: ["low", "high"],
};

test("choices list roles first, then each provider's models", () => {
  const items = pickerItems(options);
  assert.deepEqual(
    items.map((item) => `${item.group}/${item.label}`),
    ["Roles/default", "Roles/smart", "Roles/embeddings", "Den/small", "Den/tiny", "Cloud/big"],
  );
  assert.equal(new Set(items.map((item) => item.key)).size, items.length);
});

test("each choice says whether it is local or hosted, or unknown for an unset role", () => {
  const items = pickerItems(options);
  assert.deepEqual(
    items.map((item) => localityWord(item.isLocal)),
    ["local", "hosted", "unknown", "local", "local", "hosted"],
  );
  assert.equal(items[0]?.detail, "small · Den");
  assert.equal(items[2]?.detail, "not set up");
});

test("the current value is described even when it is not among the options", () => {
  assert.deepEqual(describePick({ kind: "role", role: "smart" }, options), { label: "smart", detail: "big · Cloud", isLocal: false });
  assert.deepEqual(describePick({ kind: "model", provider_id: "den", model: "unlisted" }, options), { label: "unlisted", detail: "Den", isLocal: true });
  assert.deepEqual(describePick({ kind: "model", provider_id: "gone", model: "x" }, options), { label: "x", detail: "gone", isLocal: null });
  assert.deepEqual(describePick({ kind: "role", role: "new" }, options), { label: "new", detail: "not set up", isLocal: null });
});

test("a role and a model with the same name are different targets", () => {
  assert.notEqual(targetKey({ kind: "role", role: "small" }), targetKey({ kind: "model", provider_id: "den", model: "small" }));
  assert.equal(sameTarget({ kind: "role", role: "default" }, { kind: "role", role: "default" }), true);
  assert.equal(sameTarget({ kind: "model", provider_id: "den", model: "a" }, { kind: "model", provider_id: "cloud", model: "a" }), false);
});

test("arrow keys wrap, Home and End jump, and other keys do nothing", () => {
  assert.equal(moveHighlight("ArrowDown", -1, 6), 0);
  assert.equal(moveHighlight("ArrowDown", 5, 6), 0);
  assert.equal(moveHighlight("ArrowUp", 0, 6), 5);
  assert.equal(moveHighlight("ArrowUp", -1, 6), 5);
  assert.equal(moveHighlight("Home", 3, 6), 0);
  assert.equal(moveHighlight("End", 3, 6), 5);
  assert.equal(moveHighlight("PageDown", 1, 20), 9);
  assert.equal(moveHighlight("PageUp", 3, 20), 0);
  assert.equal(moveHighlight("a", 3, 6), null);
  assert.equal(moveHighlight("ArrowDown", 0, 0), null);
});

test("typing letters finds the next choice that starts with them, wrapping around", () => {
  const items = pickerItems(options);
  assert.equal(typeahead(items, "s", 0), 1);
  assert.equal(typeahead(items, "s", 1), 3);
  assert.equal(typeahead(items, "s", 3), 1);
  assert.equal(typeahead(items, "TI", -1), 4);
  assert.equal(typeahead(items, "zz", 0), null);
  assert.equal(typeahead([], "a", 0), null);
});

test("options built from the server's data map each role and list only enabled providers", () => {
  const built = buildPickerOptions(
    [
      { role: "default", primary: { provider_id: "den", model: "small" }, inherits_default: false },
      { role: "cheap", primary: null, inherits_default: true },
      { role: "smart", primary: { provider_id: "gone", model: "big" }, inherits_default: false },
      { role: "embeddings", primary: null, inherits_default: false },
    ],
    [
      { id: "den", label: "Den", enabled: true, local: true },
      { id: "cloud", label: "Cloud", enabled: false, local: false },
    ],
    { den: ["small", "tiny"] },
    ["low"],
  );
  assert.deepEqual(built.roles, [
    { role: "default", provider_label: "Den", model: "small", local: true },
    { role: "cheap", provider_label: "Den", model: "small", local: true },
    { role: "smart", provider_label: "gone", model: "big", local: null },
    { role: "embeddings", provider_label: null, model: null, local: null },
  ]);
  assert.deepEqual(built.providers, [{ id: "den", label: "Den", local: true, models: ["small", "tiny"] }]);
  assert.deepEqual(built.efforts, ["low"]);
});

test("the list stays inside the window when the control is near the right edge", () => {
  // Plenty of room: the list opens flush with the control.
  assert.equal(popupShift(40, 1200, 288, 12), 0);
  // A 380px window with the control at the right: moved left just enough to fit.
  assert.equal(popupShift(150, 380, 288, 12), -70);
  // In a window narrower than the list it starts at the left margin, and it never moves right.
  assert.equal(popupShift(200, 280, 288, 12), -188);
  assert.equal(popupShift(4, 380, 288, 12), 0);
});
