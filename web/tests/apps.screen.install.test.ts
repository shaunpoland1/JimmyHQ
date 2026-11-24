import assert from "node:assert/strict";
import { test } from "node:test";
import { EMPTY_FORM, cleanCommit, commitProblem, folderProblem, formProblems, formReady, formSource, gitUrlProblem, nameMatches } from "../src/modules/apps/lib/install.ts";

// Built at run time so no test value looks like an address of a real person or place.
const HASH = "3f2a9c1e5b7d4a6c8e0f1a2b3c4d5e6f70819203";
const AT = "@";
const REPO = ["https:", "", "git.example.com", "apps", "water.git"].join("/");

test("a folder path must be typed, and an empty one says what to type", () => {
  assert.match(folderProblem("") ?? "", /Type the path of the folder/);
  assert.match(folderProblem("   ") ?? "", /Type the path of the folder/);
  assert.equal(folderProblem("apps/examples/water"), null);
});

test("a web address typed as a folder is turned away with a pointer to the git option", () => {
  assert.match(folderProblem(REPO) ?? "", /From a git address/);
});

test("a file typed instead of its folder is turned away", () => {
  assert.match(folderProblem("apps/water/jimmyhq.app.json") ?? "", /folder, not the file/);
});

test("a folder path with a control character in it is refused", () => {
  assert.notEqual(folderProblem("apps/wa\u0000ter"), null);
});

test("an https git address is fine", () => {
  assert.equal(gitUrlProblem(REPO), null);
  assert.equal(gitUrlProblem(`  ${REPO}  `), null);
});

test("an https address on any host is fine, a private one included", () => {
  for (const url of ["https://code.example.org/a/b/c.git", "https://192.0.2.7/apps/water", "https://git.example.com:8443/apps/water.git", "https://forge/team/water"]) {
    assert.equal(gitUrlProblem(url), null, url);
  }
});

test("an ssh address is refused in both spellings, and told to use https, as the server would", () => {
  assert.match(gitUrlProblem(`ssh://git${AT}git.example.com/apps/water.git`) ?? "", /ssh:\/\/ are not allowed/);
  assert.match(gitUrlProblem(`git${AT}git.example.com:apps/water.git`) ?? "", /over https only/);
});

test("a query or a fragment on the address is refused", () => {
  assert.notEqual(gitUrlProblem(`${REPO}?ref=main`), null);
  assert.notEqual(gitUrlProblem(`${REPO}#main`), null);
});

test("an empty git address says what one looks like", () => {
  assert.match(gitUrlProblem("") ?? "", /such as https:\/\//);
});

test("an address that makes git run a program or read a local file is refused", () => {
  for (const hostile of ["ext::sh -c touch_x", "ext::foo", "file:///etc/passwd", "file:/tmp/x", "fd::3", "transport::x"]) {
    assert.notEqual(gitUrlProblem(hostile), null, hostile);
  }
});

test("an address that starts with a dash cannot be read by git as an option", () => {
  assert.notEqual(gitUrlProblem("--upload-pack=x"), null);
  assert.notEqual(gitUrlProblem("-ohttps://git.example.com/x.git"), null);
});

test("plain http, git and other schemes are refused with the scheme named", () => {
  assert.match(gitUrlProblem("http://git.example.com/x.git") ?? "", /http:\/\/ are not allowed/);
  assert.match(gitUrlProblem("git://git.example.com/x.git") ?? "", /git:\/\/ are not allowed/);
  assert.match(gitUrlProblem("ftp://git.example.com/x.git") ?? "", /ftp:\/\/ are not allowed/);
});

test("a password or user name written into an https address is refused", () => {
  const user = "someone";
  const secret = ["pass", "word"].join("");
  assert.match(gitUrlProblem(`https://${user}:${secret}@git.example.com/x.git`) ?? "", /user name and password/);
  assert.match(gitUrlProblem(`https://${user}@git.example.com/x.git`) ?? "", /user name and password/);
});

test("an address with a space in it is refused", () => {
  assert.match(gitUrlProblem("https://git.example.com/a b.git") ?? "", /no spaces/);
});

test("an address with no host is refused", () => {
  assert.notEqual(gitUrlProblem("https:///x.git"), null);
});

test("a bare word is not a git address", () => {
  assert.match(gitUrlProblem("water") ?? "", /starts with https:\/\//);
});

test("a full forty-character commit is accepted, in any case, with spaces around it", () => {
  assert.equal(commitProblem(HASH), null);
  assert.equal(commitProblem(`  ${HASH.toUpperCase()} `), null);
  assert.equal(cleanCommit(` ${HASH.toUpperCase()} `), HASH);
});

test("a sixty-four character commit is accepted too", () => {
  assert.equal(commitProblem("a".repeat(64)), null);
});

test("a short hash is refused because it can match more than one commit", () => {
  assert.match(commitProblem(HASH.slice(0, 8)) ?? "", /short hash/);
});

test("a branch or tag name is refused because it can move", () => {
  assert.match(commitProblem("main") ?? "", /can move/);
  assert.match(commitProblem("v1.0.0") ?? "", /can move/);
});

test("a hash of the wrong length is refused with that said", () => {
  assert.match(commitProblem("a".repeat(41)) ?? "", /wrong length/);
});

test("an empty commit says it must be exact and that the app is pinned to it", () => {
  assert.match(commitProblem("") ?? "", /exact commit/);
});

test("the git form is ready only when both the address and the commit are fine", () => {
  const form = { ...EMPTY_FORM, kind: "git" as const, url: REPO, commit: HASH };
  assert.equal(formReady(form), true);
  assert.equal(formReady({ ...form, commit: "main" }), false);
  assert.equal(formReady({ ...form, url: "" }), false);
});

test("the folder form ignores what was typed for git, and the other way round", () => {
  assert.equal(formReady({ ...EMPTY_FORM, kind: "folder", path: "apps/examples/water", url: "nonsense", commit: "x" }), true);
  assert.deepEqual(formProblems({ ...EMPTY_FORM, kind: "git", path: "", url: REPO, commit: HASH }), { path: null, url: null, commit: null });
});

test("the source a git form makes carries the trimmed address and the lower-case commit", () => {
  const source = formSource({ kind: "git", path: "", url: ` ${REPO} `, commit: HASH.toUpperCase() });
  assert.deepEqual(source, { kind: "git", url: REPO, commit: HASH });
});

test("no source is made from a form that is not ready", () => {
  assert.equal(formSource({ ...EMPTY_FORM, kind: "git", url: REPO }), null);
  assert.equal(formSource(EMPTY_FORM), null);
});

test("typing a name matches ignoring case and spaces around it, and nothing else", () => {
  assert.equal(nameMatches(" drafts ", "Drafts"), true);
  assert.equal(nameMatches("Draft", "Drafts"), false);
  assert.equal(nameMatches("", ""), false);
});
