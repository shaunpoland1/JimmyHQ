import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ARCHIVED_STAYS,
  ARCHIVED_WILL_GO,
  ARCHIVE_HINT,
  CANNOT_UNDO,
  DELETE_NOTE,
  archivedNotice,
  daysUntilDeleted,
  deletedForGoodNotice,
  deletionMark,
  deletionSentence,
  whatIsErased,
  whatStays,
} from "../src/modules/chat/lib/delete.ts";

const DAY = 86_400_000;
const NOW = 1_800_000_000_000;
const conv = (over = {}) => ({ messages: 12, recorded: true, off_record_messages: 0, ...over });

test("the confirm dialog says what is erased, with how many messages", () => {
  const lines = whatIsErased(conv());
  assert.match(lines[0] ?? "", /^12 messages: everything you and the model said here, and the title\.$/);
  assert.match(lines.join(" "), /Tape.*overwritten in the database file/);
  assert.match(lines.join(" "), /Quotes.*memory.*Bench cards/);
  assert.match(lines.join(" "), /receipts of its web lookups.*searched for.*pages were read/);
  assert.match(whatIsErased(conv({ messages: 1 }))[0] ?? "", /^1 message:/);
  assert.match(whatIsErased(conv({ messages: 0 }))[0] ?? "", /Nothing has been said in it yet/);
});

test("the confirm dialog says that facts memory already saved are kept, and that it cannot be undone", () => {
  const stays = whatStays(conv());
  assert.match(stays[0] ?? "", /^Facts memory has already saved from this conversation\./);
  assert.match(stays.join(" "), /receipts.*token counts.*no words/);
  assert.match(stays.join(" "), /backup of the data folder taken earlier/);
  assert.equal(CANNOT_UNDO, "This cannot be undone.");
});

test("a conversation that was never on the record is not described in terms of the Tape or memory", () => {
  const quiet = conv({ recorded: false, off_record_messages: 12 });
  assert.doesNotMatch(whatIsErased(quiet).join(" "), /Tape|memory/);
  assert.match(whatIsErased(quiet).join(" "), /off the record, from its own store/);
  assert.doesNotMatch(whatStays(quiet).join(" "), /Facts|receipts/);
  assert.match(whatIsErased(conv({ off_record_messages: 3 })).join(" "), /Tape.*off the record/);
});

test("archiving is never described as deleting", () => {
  assert.match(ARCHIVE_HINT, /^Put away: .*Nothing is deleted\..*Delete for good…/);
  assert.match(DELETE_NOTE, /Archiving \(putting it away\) only hides/);
  const where = "To delete it for good, open it under “Show archived” and choose Delete for good… in its ⋯ menu.";
  assert.equal(archivedNotice("“Plans”", null, NOW), `Put away “Plans”. Nothing was deleted. ${where}`);
  assert.equal(archivedNotice("“Plans”", NOW + 31 * DAY, NOW, "en-GB", "UTC"), `Put away “Plans”. It is not deleted: that happens on 15 February 2027 unless you restore it first. ${where}`);
  assert.match(ARCHIVED_STAYS, /^Archived is not deleted/);
});

test("an archived conversation shows the whole days left before it is deleted", () => {
  assert.equal(daysUntilDeleted(null, NOW), null);
  assert.equal(daysUntilDeleted(undefined, NOW), null);
  assert.equal(daysUntilDeleted(NOW + 30 * DAY, NOW), 30);
  assert.equal(daysUntilDeleted(NOW + 1, NOW), 1, "part of a day is still a day left");
  assert.equal(daysUntilDeleted(NOW - DAY, NOW), 0);
  assert.equal(deletionMark(30), "30 d left");
  assert.equal(deletionMark(0), "due");
});

test("an archived conversation says the day it will be deleted, and that a grace period comes first", () => {
  assert.equal(deletionSentence(NOW + 31 * DAY, NOW, "en-GB", "UTC"), "Will be deleted for good on 15 February 2027. Restore it to keep it.");
  assert.match(deletionSentence(NOW - 1, NOW), /within the hour/);
  assert.match(ARCHIVED_WILL_GO, /on the day shown.*grace period.*Changing the setting starts the grace period again/);
});

test("the notice afterwards says how many messages went, or that it was already gone", () => {
  const done = { finished: true, settled: true };
  assert.equal(deletedForGoodNotice({ deleted: true, messages: 4, ...done }), "Conversation deleted for good, with its 4 messages.");
  assert.equal(deletedForGoodNotice({ deleted: true, messages: 0, ...done }), "Conversation deleted for good.");
  assert.equal(deletedForGoodNotice({ deleted: false, messages: 0, ...done }), "That conversation had already been deleted.");
});

test("the notice says so when something is still to be cleared, and never claims more than is true", () => {
  assert.match(deletedForGoodNotice({ deleted: true, messages: 4, finished: true, settled: false }), /4 messages\. The last traces of its text are cleared from the files within the hour\.$/);
  assert.match(deletedForGoodNotice({ deleted: true, messages: 4, finished: false, settled: false }), /Memory could not be reached.*tried again within the hour\.$/);
});
