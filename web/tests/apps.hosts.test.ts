// Hosts the owner gives an app on its Settings page: where requests go, what is read back,
// and that a secret kept for a host is never kept or shown here.

import assert from "node:assert/strict";
import { test } from "node:test";
import { addQuestion, hostPath, hostProblem, hostSecretPath, hostsPath, readHosts, removeQuestion } from "../src/modules/apps/lib/hosts.ts";

const KEY = "sk-test-SECRET-0123456789";
const ASKED = { declared: { purpose: "Publish drafts to your own site.", max: 2 }, hosts: [] };

test("a host is sent only to its own app's address, and only when it looks like one exact name", () => {
  assert.equal(hostsPath("articles"), "/api/apps/articles/hosts");
  assert.equal(hostPath("articles", "blog.example.com"), "/api/apps/articles/hosts/blog.example.com");
  assert.equal(hostSecretPath("articles", "blog.example.com", "site_password"), "/api/apps/articles/hosts/blog.example.com/secrets/site_password");
  for (const host of ["../x", "blog.example.com/x", "Blog.example.com", "*.example.com", "192.0.2.1", "localhost", "blog.example.com:8443", ""]) {
    assert.equal(hostPath("articles", host), null, host);
  }
  assert.equal(hostSecretPath("articles", "blog.example.com", "../x"), null);
  assert.equal(hostsPath("_installed"), null);
});

test("what the server lists is read defensively, and a value it never sends is never kept", () => {
  const read = readHosts({
    declared: ASKED.declared,
    hosts: [
      { host: "blog.example.com", added_at: 5, secrets: [{ name: "site_password", label: "Password", set: true, value: KEY }] },
      { host: "../evil" },
      null,
    ],
  });
  assert.deepEqual(read, {
    declared: { ...ASKED.declared, next: null },
    hosts: [{ host: "blog.example.com", addedAt: 5, secrets: [{ name: "site_password", label: "Password", help: "", set: true }] }],
  });
  assert.equal(JSON.stringify(read).includes(KEY), false);
  assert.deepEqual(readHosts({ declared: { purpose: "x", max: 0 } }), { declared: null, hosts: [] });
  assert.deepEqual(readHosts("nonsense"), { declared: null, hosts: [] });
});

test("a wildcard, another scheme, an empty field or one too many is stopped with a sentence", () => {
  for (const typed of ["", "  ", "*.example.com", "http://blog.example.com", "ftp://blog.example.com"]) {
    const problem = hostProblem(typed, ASKED);
    assert.ok(problem && problem.includes("Nothing was added"), typed);
  }
  const full = { ...ASKED, hosts: [{ host: "a.example.com", addedAt: 1, secrets: [] }, { host: "b.example.com", addedAt: 2, secrets: [] }] };
  assert.match(hostProblem("blog.example.com", full) ?? "", /at most 2/);
  assert.equal(hostProblem("https://blog.example.com", ASKED), null);
  assert.equal(hostProblem("blog.example.com", ASKED), null);
});

test("adding and removing each ask first, in plain words that name the host", () => {
  const adding = addQuestion("Articles", "HTTPS://Blog.Example.com/");
  assert.equal(adding.title, "Let Articles reach blog.example.com?");
  assert.match(adding.message, /send data to and read data from blog\.example\.com/);
  const removing = removeQuestion("Articles", "blog.example.com");
  assert.equal(removing.title, "Remove blog.example.com?");
  assert.match(removing.message, /cleared/);
});
