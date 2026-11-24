import assert from "node:assert/strict";
import { test } from "node:test";
import { parseThemePref, resolveTheme, themeLabel } from "../src/lib/theme.ts";

test("resolveTheme follows the system only for the system preference", () => {
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
});

test("parseThemePref accepts only known values", () => {
  assert.equal(parseThemePref("dark"), "dark");
  assert.equal(parseThemePref("system"), "system");
  assert.equal(parseThemePref("sepia"), null);
  assert.equal(parseThemePref(null), null);
  assert.equal(parseThemePref(1), null);
});

test("themeLabel is human readable", () => {
  assert.equal(themeLabel("system"), "System");
});
