import assert from "node:assert/strict";
import { test } from "node:test";
import { askedSummary, groupLines, groupOf, hostsOf } from "../src/modules/apps/lib/permissions.ts";
import { OWN, RUNS, line } from "./apps.fixtures.ts";

test("lines are grouped most sensitive group first, whatever order they arrive in", () => {
  const groups = groupLines([OWN, line("tasks.read"), line("net:api.example.com"), RUNS, line("tasks.write")]);
  assert.deepEqual(
    groups.map((group) => group.key),
    ["warning", "outside", "reads", "acts", "own"],
  );
});

test("the server's order is kept inside a group", () => {
  const groups = groupLines([line("jobs"), line("bench.file"), line("tasks.write")]);
  assert.deepEqual(groups[0]?.lines.map((item) => item.id), ["jobs", "bench.file", "tasks.write"]);
});

test("the runs-as-you line is always in a group of its own, first", () => {
  const groups = groupLines([line("tape.read"), RUNS]);
  assert.equal(groups[0]?.key, "warning");
  assert.equal(groups[0]?.lines.length, 1);
});

test("a line the server marks strong is a warning even if its permission is not known here", () => {
  assert.equal(groupOf({ id: "future.thing", sentence: "Does a thing.", strong: true }), "warning");
});

test("a permission this screen has never heard of is still shown, under also", () => {
  const groups = groupLines([line("future.thing")]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0]?.key, "other");
  assert.equal(groups[0]?.lines[0]?.sentence, "Sentence for future.thing.");
});

test("a host line is grouped with what can send things away, and its host is listed", () => {
  const lines = [line("net:api.example.com"), line("model.ask"), line("net:cdn.example.com")];
  assert.equal(groupLines(lines)[0]?.key, "outside");
  assert.deepEqual(hostsOf(lines), ["api.example.com", "cdn.example.com"]);
});

test("the count of permissions leaves out the lines JimmyHQ adds itself", () => {
  assert.equal(askedSummary([OWN]), "Asks for nothing beyond its own data");
  assert.equal(askedSummary([line("tasks.write"), OWN]), "Asks for 1 permission");
  assert.equal(askedSummary([line("jobs"), line("model.ask"), OWN]), "Asks for 2 permissions");
});

test("a full app's count says it runs as you, even when it asks for nothing else", () => {
  assert.equal(askedSummary([RUNS, OWN]), "Runs as you, and asks for nothing else");
  assert.equal(askedSummary([RUNS, line("jobs"), OWN]), "Runs as you, and asks for 1 more permission");
  assert.equal(askedSummary([RUNS, line("jobs"), line("model.ask"), OWN]), "Runs as you, and asks for 2 more permissions");
});
