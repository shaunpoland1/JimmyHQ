import assert from "node:assert/strict";
import { test } from "node:test";
import type { BoardPost, RoomLine } from "../src/modules/village/api.ts";
import { agoText, boardCounts, newestLines, postKindWords, sortPosts, wasUnanswered, whoIs } from "../src/modules/village/lib/room.ts";

const NOW = 1_800_000_000_000;
const MINUTE = 60_000;

test("how long ago is said in the nearest sensible unit", () => {
  assert.equal(agoText(NOW - 20_000, NOW), "just now");
  assert.equal(agoText(NOW - MINUTE, NOW), "1 minute ago");
  assert.equal(agoText(NOW - 5 * MINUTE, NOW), "5 minutes ago");
  assert.equal(agoText(NOW - 3 * 60 * MINUTE, NOW), "3 hours ago");
  assert.equal(agoText(NOW - 3 * 24 * 60 * MINUTE, NOW), "3 days ago");
  assert.equal(agoText(null, NOW), "some time ago");
});

test("a time sent in seconds is read the same as one in milliseconds", () => {
  assert.equal(agoText((NOW - 5 * MINUTE) / 1000, NOW), "5 minutes ago");
});

test("a speaker is named, or shown by id once they are gone, or called someone", () => {
  const everyone = [{ id: "agent-a", name: "Wren", role: "writer" }];
  assert.equal(whoIs("agent-a", everyone), "Wren");
  assert.equal(whoIs("agent-q", everyone), "Agent Q");
  assert.equal(whoIs(null, everyone), "Someone");
});

test("break-room lines are newest first, and a line nobody answered is told apart", () => {
  const lines: RoomLine[] = [
    { id: "evt-1", ts: NOW - 3 * MINUTE, agent: "agent-a", kind: "breakroom", text: "Anyone about?" },
    { id: "evt-3", ts: NOW - MINUTE, agent: "agent-b", kind: "social_unanswered", text: "(nobody answered; went back to work)" },
    { id: "evt-2", ts: NOW - 2 * MINUTE, agent: "agent-b", kind: "breakroom", text: "Here." },
  ];
  const sorted = newestLines(lines);
  assert.deepEqual(sorted.map((line) => line.id), ["evt-3", "evt-2", "evt-1"]);
  assert.equal(wasUnanswered(sorted[0] as RoomLine), true);
  assert.equal(wasUnanswered(sorted[1] as RoomLine), false);
});

test("the three kinds of board post are worded and an unknown one is not hidden", () => {
  assert.equal(postKindWords("need_help"), "Needs help");
  assert.equal(postKindWords("good_at"), "Good at");
  assert.equal(postKindWords("handoff"), "Handing over");
  assert.equal(postKindWords("new_kind"), "new kind");
  assert.equal(postKindWords(null), "Post");
});

test("open posts come first, each group newest first, and the counts add up", () => {
  const post = (id: string, ts: number, open: boolean): BoardPost => ({ id, ts, agent: "agent-a", kind: "need_help", text: id, open });
  const posts = [post("evt-1", NOW - 10 * MINUTE, true), post("evt-2", NOW - 5 * MINUTE, false), post("evt-3", NOW - MINUTE, true), post("evt-4", NOW - 20 * MINUTE, false)];
  assert.deepEqual(sortPosts(posts).map((item) => item.id), ["evt-3", "evt-1", "evt-2", "evt-4"]);
  assert.deepEqual(boardCounts(posts), { open: 2, done: 2 });
  assert.deepEqual(boardCounts(undefined), { open: 0, done: 0 });
});
