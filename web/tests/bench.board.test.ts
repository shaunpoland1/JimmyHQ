import assert from "node:assert/strict";
import { test } from "node:test";
import type { Action, Board, Card, CardListItem } from "../src/modules/bench/api.ts";
import {
  cardToPin,
  stillSnoozed,
  countByFiler,
  decideBody,
  findCard,
  flatOrder,
  groupCards,
  moveSelection,
  nextAfter,
  oldestWaitStart,
  parseSortOrder,
  placeCard,
  predictDecision,
  predictSnooze,
  predictUnsnooze,
  snoozedCards,
  sortCards,
  toListItem,
} from "../src/modules/bench/lib/board.ts";

const at = (day: number, hour: number) => new Date(2030, 4, day, hour).getTime();

const ACTIONS: Action[] = [
  { id: "approve", label: "Approve", style: "primary", needs_reason: false, closes: true, moves_to: null },
  { id: "not_yet", label: "Not yet", style: "default", needs_reason: false, closes: false, moves_to: "waiting" },
  { id: "no", label: "No", style: "danger", needs_reason: true, closes: true, moves_to: null },
];

function card(id: string, over: Partial<CardListItem> = {}): CardListItem {
  return {
    id,
    kind: "posts.draft",
    source: "Posts",
    title: `Card ${id}`,
    summary: "",
    filed_by: "agent:scout",
    subject: null,
    lane: "needs",
    actions: ACTIONS,
    filed: at(10, 9),
    activity: at(10, 9),
    needs_since: at(10, 9),
    snoozed: false,
    snoozed_until: null,
    files: 0,
    updates: 0,
    ladder: null,
    status: "open",
    ...over,
  };
}

function board(needs: CardListItem[], done: CardListItem[] = [], waiting: CardListItem[] = []): Board {
  return {
    lanes: { needs, done, waiting },
    counts: { needs: needs.length, done: done.length, waiting: waiting.length },
    needs_by_kind: {},
    needs_by_source: {},
    oldest_needs_since: null,
    oldest_needs_ms: null,
    recent: [],
    now: at(10, 12),
    snooze_choices: [],
    timezone: "local",
  };
}

test("a stored sort order is only accepted when it is one the Bench knows", () => {
  assert.equal(parseSortOrder("oldest"), "oldest");
  assert.equal(parseSortOrder("newest"), "newest");
  assert.equal(parseSortOrder("sideways"), undefined);
  assert.equal(parseSortOrder(3), undefined);
});

test("oldest first sorts by how long a card has waited, newest by last activity", () => {
  const a = card("A", { needs_since: at(10, 8), activity: at(10, 11) });
  const b = card("B", { needs_since: at(10, 9), activity: at(10, 9) });
  const c = card("C", { needs_since: null, filed: at(9, 7), activity: at(10, 10) });
  assert.deepEqual(sortCards([a, b, c], "oldest").map((item) => item.id), ["C", "A", "B"]);
  assert.deepEqual(sortCards([a, b, c], "newest").map((item) => item.id), ["A", "C", "B"]);
});

test("cards with the same time keep a steady order", () => {
  const cards = [card("B"), card("A"), card("C")];
  assert.deepEqual(sortCards(cards, "oldest").map((item) => item.id), ["A", "B", "C"]);
  assert.deepEqual(sortCards(cards, "newest").map((item) => item.id), ["A", "B", "C"]);
});

test("cards are grouped by source, then by local day, in list order", () => {
  const cards = [
    card("A", { source: "Posts", needs_since: at(10, 8) }),
    card("B", { source: "Household", needs_since: at(10, 9) }),
    card("C", { source: "Posts", needs_since: at(9, 23) }),
    card("D", { source: "Posts", needs_since: at(10, 10) }),
  ];
  const groups = groupCards(cards, "oldest");
  assert.deepEqual(groups.map((group) => [group.source, group.count]), [["Posts", 3], ["Household", 1]]);
  assert.deepEqual(groups[0]?.days.map((day) => [day.day, day.cards.map((item) => item.id)]), [
    ["2030-05-09", ["C"]],
    ["2030-05-10", ["A", "D"]],
  ]);
  assert.deepEqual(flatOrder(groups), ["C", "A", "D", "B"]);
});

test("after a card leaves, the next one is the one after it, else the one before", () => {
  assert.equal(nextAfter(["A", "B", "C"], "B"), "C");
  assert.equal(nextAfter(["A", "B", "C"], "C"), "B");
  assert.equal(nextAfter(["A"], "A"), null);
  assert.equal(nextAfter(["A", "B"], "Z"), "A");
  assert.equal(nextAfter([], "A"), null);
});

test("moving the selection stops at both ends and starts at the top", () => {
  const order = ["A", "B", "C"];
  assert.equal(moveSelection(order, "A", 1), "B");
  assert.equal(moveSelection(order, "C", 1), "C");
  assert.equal(moveSelection(order, "A", -1), "A");
  assert.equal(moveSelection(order, null, 1), "A");
  assert.equal(moveSelection(order, "gone", -1), "A");
  assert.equal(moveSelection([], "A", 1), null);
});

test("the summary counts cards per filer, most first", () => {
  const cards = [card("A"), card("B", { filed_by: "agent:clerk" }), card("C"), card("D", { filed_by: "owner" })];
  assert.deepEqual(countByFiler(cards), [
    { filer: "agent:scout", count: 2 },
    { filer: "agent:clerk", count: 1 },
    { filer: "owner", count: 1 },
  ]);
  assert.equal(oldestWaitStart([card("A", { needs_since: at(10, 8) }), card("B", { needs_since: at(9, 8) })]), at(9, 8));
  assert.equal(oldestWaitStart([]), null);
});

test("snoozed cards are listed soonest back first", () => {
  const view = board([], [], [
    card("A", { snoozed: true, snoozed_until: at(12, 9) }),
    card("B", { lane: "waiting" }),
    card("C", { snoozed: true, snoozed_until: at(11, 9) }),
  ]);
  assert.deepEqual(snoozedCards(view).map((item) => item.id), ["C", "A"]);
});

test("a closing decision takes the card off the board and the counts follow", () => {
  const view = board([card("A"), card("B")]);
  const approve = ACTIONS[0]!;
  const predicted = predictDecision(card("A"), approve, at(10, 13));
  assert.equal(predicted, null);
  const next = placeCard(view, "A", predicted);
  assert.deepEqual(next.lanes.needs.map((item) => item.id), ["B"]);
  assert.deepEqual(next.counts, { needs: 1, done: 0, waiting: 0 });
  assert.equal(findCard(next, "A"), undefined);
  assert.equal(view.lanes.needs.length, 2, "the original board is untouched");
});

test("a moving decision puts the card in its new lane", () => {
  const view = board([card("A"), card("B")]);
  const predicted = predictDecision(card("A"), ACTIONS[1]!, at(10, 13));
  assert.equal(predicted?.lane, "waiting");
  assert.equal(predicted?.activity, at(10, 13));
  const next = placeCard(view, "A", predicted);
  assert.deepEqual(next.counts, { needs: 1, done: 0, waiting: 1 });
  assert.equal(next.lanes.waiting[0]?.id, "A");
});

test("an action that neither closes nor moves leaves the card where it is and clears a snooze", () => {
  const nudge: Action = { id: "nudge", label: "Nudge", style: "default", needs_reason: false, closes: false, moves_to: null };
  const snoozed = card("A", { snoozed: true, snoozed_until: at(12, 9) });
  const predicted = predictDecision(snoozed, nudge, at(10, 13));
  assert.equal(predicted?.snoozed, false);
  const next = placeCard(board([], [], [snoozed]), "A", predicted);
  assert.deepEqual(next.counts, { needs: 1, done: 0, waiting: 0 });
});

test("snoozing lists the card under waiting and unsnoozing brings it back", () => {
  const view = board([card("A")]);
  const snoozed = placeCard(view, "A", predictSnooze(card("A"), at(11, 9)));
  assert.deepEqual(snoozed.counts, { needs: 0, done: 0, waiting: 1 });
  assert.equal(snoozed.lanes.waiting[0]?.lane, "needs", "the stored lane does not change");
  const back = placeCard(snoozed, "A", predictUnsnooze(snoozed.lanes.waiting[0]!, at(10, 14)));
  assert.deepEqual(back.counts, { needs: 1, done: 0, waiting: 0 });
  assert.equal(back.lanes.needs[0]?.needs_since, at(10, 14));
});

test("a closed card from the server is not put back on the board", () => {
  const closed = card("A", { status: "decided" });
  assert.deepEqual(placeCard(board([card("A")]), "A", closed).counts, { needs: 0, done: 0, waiting: 0 });
});

test("a full card becomes its list form with files as a count", () => {
  const full: Card = {
    ...card("A"),
    files: [{ index: 0, name: "plan.md" }, { index: 1, name: "notes.md" }],
    body: "text",
    body_missing: false,
    why: null,
    details: [],
    history: [],
    decision: null,
  };
  const item = toListItem(full);
  assert.equal(item.files, 2);
  assert.equal("body" in item, false);
  assert.equal("history" in item, false);
});

test("the last card is not pinned again while its decision is being sent", () => {
  // Nothing chosen: the first card in the list is shown.
  assert.equal(cardToPin(null, "A", null), "A");
  // The only card was just decided and is on its way out: the pane must empty, not snap back to it.
  assert.equal(cardToPin(null, "A", "A"), null);
  // A different card being sent does not stop the first one being shown.
  assert.equal(cardToPin(null, "A", "B"), "A");
  // A chosen card stays chosen whatever arrives at the top of the list.
  assert.equal(cardToPin("C", "A", null), "C");
  assert.equal(cardToPin(null, null, null), null);
});

test("a snooze that has run out no longer counts, even if the card was read while it was snoozed", () => {
  const until = at(12, 9);
  assert.equal(stillSnoozed({ snoozed: true, snoozed_until: until }, until - 1), true);
  assert.equal(stillSnoozed({ snoozed: true, snoozed_until: until }, until), false);
  assert.equal(stillSnoozed({ snoozed: true, snoozed_until: until }, until + 60_000), false);
  assert.equal(stillSnoozed({ snoozed: false, snoozed_until: null }, until), false);
  // Snoozed with the time not known yet (the server is choosing it) still counts.
  assert.equal(stillSnoozed({ snoozed: true, snoozed_until: null }, until), true);
});

test("a decision names the revision of the card that was on screen", () => {
  const approve = ACTIONS[0]!;
  assert.deepEqual(decideBody({ updates: 0 }, approve, "  ", ""), { action: "approve", note: null, reason: null, seen_updates: 0 });
  // The filer has updated the card three times; the click is for that third revision and no later one.
  assert.deepEqual(decideBody({ updates: 3 }, ACTIONS[2]!, " Fine. ", " Too long. "), {
    action: "no",
    note: "Fine.",
    reason: "Too long.",
    seen_updates: 3,
  });
});
