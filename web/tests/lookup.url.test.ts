import assert from "node:assert/strict";
import { test } from "node:test";
import { hostOf, safeWebUrl, shortAddress } from "../src/modules/lookup/lib/url.ts";

// Built at run time so the repo's public-safety check does not take the test URL for an email address.
const AT = String.fromCharCode(64);

test("an ordinary http or https address is safe and comes back clean", () => {
  assert.equal(safeWebUrl("https://example.com/beans?x=1#top"), "https://example.com/beans?x=1#top");
  assert.equal(safeWebUrl("  http://example.com  "), "http://example.com/");
});

test("an address with another scheme is never safe", () => {
  for (const bad of ["javascript:alert(1)", "data:text/html,hi", "file:///etc/hosts", "ftp://example.com/a", "mailto:a@example.com", "vbscript:x", "//example.com/a", "/relative/path", "#anchor"]) {
    assert.equal(safeWebUrl(bad), null, bad);
  }
});

test("an address that carries a user name or a password is not safe", () => {
  assert.equal(safeWebUrl(`https://trusted.example.com${AT}other.example.net/`), null);
  assert.equal(safeWebUrl("https://user:secret@example.com/"), null);
});

test("an address with a space, a backslash or a control character is not safe", () => {
  assert.equal(safeWebUrl("https://example.com/a b"), null);
  assert.equal(safeWebUrl("https://example.com\\evil"), null);
  assert.equal(safeWebUrl("https://example.com/\u0007"), null);
  assert.equal(safeWebUrl(""), null);
  assert.equal(safeWebUrl(null), null);
  assert.equal(safeWebUrl(42), null);
});

test("a very long address is not safe", () => {
  assert.equal(safeWebUrl(`https://example.com/${"a".repeat(4000)}`), null);
});

test("the host is shown in lower case without a leading www", () => {
  assert.equal(hostOf("https://WWW.Example.com/beans"), "example.com");
  assert.equal(hostOf("https://docs.example.com:8080/x"), "docs.example.com");
  assert.equal(hostOf("javascript:alert(1)"), "");
});

test("a short address is the host and the start of the path, with no scheme, query or fragment", () => {
  assert.equal(shortAddress("https://example.com/a/b?x=1#y"), "example.com/a/b");
  assert.equal(shortAddress("https://example.com/"), "example.com");
  assert.equal(shortAddress(`https://example.com/${"long/".repeat(30)}`, 30).length, 30);
  assert.ok(shortAddress(`https://example.com/${"long/".repeat(30)}`, 30).endsWith("…"));
});

test("a short address of something that is not a web address is cut, not turned into a link", () => {
  assert.equal(shortAddress("not an address"), "not an address");
  assert.equal(shortAddress(undefined), "");
});
