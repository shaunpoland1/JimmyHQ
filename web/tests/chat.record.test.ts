import assert from "node:assert/strict";
import { test } from "node:test";
import type { Conversation, Message } from "../src/modules/chat/api.ts";
import {
  NOTHING_TO_DELETE,
  deleteWarning,
  deletedNotice,
  dividerText,
  dropConversation,
  hasOffRecord,
  isChatReceipt,
  lastOpenAfter,
  newConversationRecord,
  offTheFeed,
  recordHelp,
  threadRefreshMs,
  threadRows,
} from "../src/modules/chat/lib/record.ts";

const ID = (n: number) => `01JABCDEFGHJKMNPQRSTVWXY${String(n).padStart(2, "0")}`;

function said(n: number, onRecord: boolean): Message {
  return { id: ID(n), role: "user", created: n, text: `message ${n}`, on_record: onRecord };
}

function shape(rows: ReturnType<typeof threadRows>): string[] {
  return rows.map((row) => (row.kind === "divider" ? `-- ${dividerText(row.onRecord)}` : row.message.id.slice(-2)));
}

function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    id: ID(1),
    title: "Plans",
    title_source: "cut",
    target: { kind: "role", role: "default" },
    effort: null,
    local_only: false,
    use_memory: true,
    created: 1,
    updated: 2,
    messages: 4,
    archived: false,
    on_record: true,
    recorded: true,
    off_record_messages: 0,
    off_record_title: false,
    streaming: false,
    archived_at: null,
    deletes_at: null,
    ...over,
  };
}

test("a divider stands wherever what follows is kept differently from what came before", () => {
  const messages = [said(1, true), said(2, true), said(3, false), said(4, false), said(5, true)];
  assert.deepEqual(shape(threadRows(messages, true, true)), ["01", "02", "-- Off the record from here", "03", "04", "-- On the record from here", "05"]);
});

test("a conversation with nothing off the record has no dividers at all", () => {
  assert.deepEqual(shape(threadRows([said(1, true), said(2, true)], true, true)), ["01", "02"]);
  assert.deepEqual(shape(threadRows([], true, true)), []);
});

test("a conversation that begins off the record says so at the top, but only once its beginning is loaded", () => {
  const messages = [said(1, false), said(2, false)];
  assert.deepEqual(shape(threadRows(messages, true, false)), ["-- Off the record from here", "01", "02"]);
  assert.deepEqual(shape(threadRows(messages, false, false)), ["01", "02"], "older messages are still to come: nothing is claimed about them");
  assert.deepEqual(shape(threadRows([], true, false)), ["-- Off the record from here"]);
});

test("a flip nothing has been said after still shows at the end of the thread", () => {
  assert.deepEqual(shape(threadRows([said(1, true)], true, false)), ["01", "-- Off the record from here"]);
  assert.deepEqual(shape(threadRows([said(1, false)], false, true)), ["01", "-- On the record from here"]);
  assert.deepEqual(shape(threadRows([said(1, false)], false, null)), ["01"], "null leaves the end alone");
});

test("every divider has a key of its own", () => {
  const rows = threadRows([said(1, true), said(2, false), said(3, true)], true, false);
  const keys = rows.flatMap((row) => (row.kind === "divider" ? [row.key] : []));
  assert.equal(new Set(keys).size, keys.length);
  assert.equal(keys.length, 3);
});

test("the line under the composer says what the switch means, without the word the other rooms use", () => {
  assert.match(recordHelp(true), /^On the record: .*Tape.*until you delete the conversation/);
  assert.match(recordHelp(false), /^Off the record: .*apart from the Tape.*delete it for good/);
  for (const words of [recordHelp(true), recordHelp(false), NOTHING_TO_DELETE, deleteWarning(conv({ off_record_messages: 2 })), deleteWarning(conv({ recorded: false }))]) {
    assert.doesNotMatch(words, /private/i);
  }
});

test("delete is offered only when something is off the record", () => {
  assert.equal(hasOffRecord(conv()), false);
  assert.equal(hasOffRecord(conv({ off_record_messages: 1 })), true);
  assert.equal(hasOffRecord(conv({ off_record_title: true })), true);
  assert.equal(hasOffRecord(conv({ recorded: false })), true, "one never on the record can always go, even empty");
  assert.match(NOTHING_TO_DELETE, /Nothing here is off the record/);
});

test("the confirm dialog says how many messages go, that it cannot be undone, and what stays", () => {
  assert.equal(deleteWarning(conv({ off_record_messages: 1 })), "This deletes 1 off-the-record message for good. It cannot be undone. What is on the record stays; to remove that too, delete the whole conversation.");
  assert.match(deleteWarning(conv({ off_record_messages: 12, off_record_title: true })), /^This deletes 12 off-the-record messages and the title given off the record for good\. It cannot be undone\./);
  assert.match(deleteWarning(conv({ off_record_title: true })), /^This deletes the title given off the record for good/);
  const whole = deleteWarning(conv({ recorded: false, off_record_messages: 3 }));
  assert.match(whole, /the whole conversation, its 3 off-the-record messages, will be deleted for good\. This cannot be undone\.$/);
  assert.match(deleteWarning(conv({ recorded: false })), /which has no messages yet/);
});

test("the notice afterwards says what went", () => {
  assert.equal(deletedNotice(2, false), "Deleted 2 off-the-record messages for good.");
  assert.equal(deletedNotice(0, false), "Deleted what was off the record.");
  assert.equal(deletedNotice(5, true), "Conversation deleted for good.");
});

test("a conversation that is off the record is one the live feed cannot speak for", () => {
  assert.equal(offTheFeed(conv({ on_record: false })), true);
  assert.equal(offTheFeed(conv()), false);
  assert.equal(offTheFeed(undefined), false);
});

test("an open conversation is read again on a timer only while another tab's reply is being written in it", () => {
  assert.equal(threadRefreshMs(conv({ streaming: true }), false, 5000), 5000);
  assert.equal(threadRefreshMs(conv({ streaming: true, on_record: false }), false, 5000), 5000);
  // Being off the record is no reason to keep asking the server.
  assert.equal(threadRefreshMs(conv({ on_record: false }), false, 5000), false);
  assert.equal(threadRefreshMs(conv(), false, 5000), false);
  // This tab is listening to the reply itself.
  assert.equal(threadRefreshMs(conv({ streaming: true }), true, 5000), false);
  assert.equal(threadRefreshMs(undefined, false, 5000), false);
});

test("a new conversation is sent the record switch only when the owner flipped it, so the server's setting decides otherwise", () => {
  assert.deepEqual(newConversationRecord(null), {});
  assert.deepEqual(newConversationRecord(false), { on_record: false });
  assert.deepEqual(newConversationRecord(true), { on_record: true });
});

test("the conversation to come back to is never one with nothing on the record", () => {
  assert.equal(lastOpenAfter(null, { id: ID(1), recorded: true }), ID(1));
  assert.equal(lastOpenAfter(ID(1), { id: ID(2), recorded: true }), ID(2));
  // Opening one that has only ever been off the record leaves what was remembered alone...
  assert.equal(lastOpenAfter(ID(1), { id: ID(2), recorded: false }), ID(1));
  assert.equal(lastOpenAfter(null, { id: ID(2), recorded: false }), null);
  // ...and lets go of it if it was the one remembered.
  assert.equal(lastOpenAfter(ID(2), { id: ID(2), recorded: false }), null);
});

test("a deleted conversation is taken out of every page of a list and the original is untouched", () => {
  const data = { pages: [{ conversations: [conv({ id: ID(3) }), conv({ id: ID(2) })], next: "x" }, { conversations: [conv({ id: ID(1) })], next: null }], pageParams: [undefined, "x"] };
  const left = dropConversation(data, ID(2));
  assert.deepEqual(left?.pages.map((page) => page.conversations.map((c) => c.id)), [[ID(3)], [ID(1)]]);
  assert.equal(left?.pages[0]?.next, "x");
  assert.equal(data.pages[0]?.conversations.length, 2);
  assert.equal(dropConversation(undefined, ID(2)), undefined);
});

test("only the receipt of a model call that chat made counts as a sign that a conversation changed", () => {
  assert.equal(isChatReceipt("model.called", { caller: "chat.reply" }), true);
  assert.equal(isChatReceipt("model.failed", { caller: "chat.title" }), true);
  assert.equal(isChatReceipt("model.called", { caller: "bench.ladder" }), false);
  assert.equal(isChatReceipt("model.called", {}), false);
  assert.equal(isChatReceipt("model.role.set", { caller: "chat.reply" }), false);
});
