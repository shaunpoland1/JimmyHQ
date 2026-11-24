// Filling a site's form from its website: only empty fields are filled, and the one line
// under the button says which were filled and which were left alone.

import assert from "node:assert/strict";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { NO_ADDRESS, addressFrom, fillEmpty, fillSaid } from "../../apps/articles/web/lib/fill.js";

const proposal = {
  fields: { name: "Example Garden", audience: "People new to growing food", register: "Warm and plain.", length: "About 900 words", headings: "", closing: "A line on where to read more" },
  samples: ["First sample.", "Second sample."],
};
const blank = { name: "", audience: "", register: "", samples: [], length: "", headings: "", closing: "" };

test("every empty field is filled from the proposal", () => {
  const got = fillEmpty(blank, proposal);
  assert.deepEqual(got.values, { name: "Example Garden", audience: "People new to growing food", register: "Warm and plain.", samples: ["First sample.", "Second sample."], length: "About 900 words", closing: "A line on where to read more" });
  assert.deepEqual(got.kept, []);
  assert.deepEqual(got.missing, ["headings"]);
});

test("a field the owner already typed in is never overwritten", () => {
  const typed = { ...blank, name: "My own name", register: "  Dry and brief.  ", samples: ["", "Mine."] };
  const got = fillEmpty(typed, proposal);
  assert.ok(!("name" in got.values) && !("register" in got.values) && !("samples" in got.values));
  assert.deepEqual(got.kept, ["name", "register", "samples"]);
  assert.deepEqual(got.filled, ["audience", "length", "closing"]);
});

test("a field of only spaces or empty sample rows counts as empty", () => {
  const got = fillEmpty({ ...blank, audience: "   ", samples: ["", "  "] }, proposal);
  assert.ok("audience" in got.values && "samples" in got.values);
});

test("a proposal with nothing in it fills nothing and says so", () => {
  const got = fillEmpty(blank, { fields: {}, samples: [] });
  assert.deepEqual(got.values, {});
  assert.match(fillSaid(got), /^Nothing was filled in\./);
});

test("the line says which fields were filled, which were left alone and which the website did not show", () => {
  const typed = { ...blank, name: "Mine" };
  const said = fillSaid(fillEmpty(typed, proposal));
  assert.match(said, /Filled in: who it writes for, how it sounds, writing samples, length and how it ends\./);
  assert.match(said, /Left alone because you had typed them: name\./);
  assert.match(said, /The website did not show: headings\./);
  assert.match(said, /Nothing is saved until you press Save\./);
  assert.match(fillSaid(fillEmpty(blank, proposal), ["The model could not describe the site."]), /could not describe the site\.$/);
});

test("the address is read as https when it has no scheme, and anything but a web address is refused", () => {
  assert.deepEqual(addressFrom(" www.example.org "), { address: "https://www.example.org" });
  assert.deepEqual(addressFrom("http://www.example.org/blog"), { address: "http://www.example.org/blog" });
  assert.equal(addressFrom("").error, NO_ADDRESS);
  assert.match(addressFrom("ftp://www.example.org").error, /only web addresses/);
  assert.match(addressFrom("file:///a").error, /Nothing was read\./);
  assert.match(addressFrom("two words").error, /no spaces/);
});
