import assert from "node:assert/strict";
import { test } from "node:test";
import type { AssistantMessage, ChatOptions, Conversation, ConversationPage, Message, StreamStart, UserMessage } from "../src/modules/chat/api.ts";
import { KEY_WIDGET_SELECTOR, canSend, composerCommand, countChars, counterFor, counterText, focusTakesKeys, insertAtCursor, offersModelSettings, refusalDetail, screenCommand } from "../src/modules/chat/lib/compose.ts";
import { runSessionEndHooks } from "../src/lib/session.ts";
import { clearDrafts, saveDrafts, savedDrafts } from "../src/modules/chat/lib/drafts.ts";
import { CHAT_LIST_KEY, listKey, listsKey } from "../src/modules/chat/keys.ts";
import { endRuns } from "../src/modules/chat/lib/runs.ts";
import type { EndableRun } from "../src/modules/chat/lib/runs.ts";
import {
  MAX_SEARCH,
  ROW_ACTION_CLASS,
  UNTITLED,
  conversationTitle,
  fallbackNote,
  filterConversations,
  flattenList,
  formatDuration,
  formatListTime,
  formatTokens,
  fromPickerTarget,
  modelReady,
  omittedNote,
  parseChatRoute,
  parseStoredId,
  placeConversation,
  replyMeta,
  searchQuery,
  titleMatches,
  toPickerOptions,
  toPickerTarget,
} from "../src/modules/chat/lib/format.ts";
import { localToggle } from "../src/modules/chat/lib/memory.ts";
import { unsetEffortLabel } from "../src/modules/models/lib/picker.ts";
import { applyReply, flattenPages, nearBottom, newLiveReply, threadView } from "../src/modules/chat/lib/reply.ts";
import type { ThreadPages } from "../src/modules/chat/lib/reply.ts";

const ID = (n: number) => `01JABCDEFGHJKMNPQRSTVWXY${String(n).padStart(2, "0")}`;

function conv(n: number, over: Partial<Conversation> = {}): Conversation {
  return {
    id: ID(n),
    title: `Conversation ${n}`,
    title_source: "cut",
    target: { kind: "role", role: "default" },
    effort: null,
    local_only: false,
    use_memory: true,
    created: 1000 * n,
    updated: 1000 * n,
    messages: 2,
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

function userMessage(n: number, text = `question ${n}`): UserMessage {
  return { id: ID(n), role: "user", created: n, text, on_record: true };
}

function reply(n: number, over: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: ID(n),
    role: "assistant",
    created: n,
    text: `answer ${n}`,
    on_record: true,
    reply_to: ID(n - 1),
    state: "complete",
    error: null,
    provider_id: "den",
    provider_type: "openai_compatible",
    model: "small",
    local: true,
    fallback_from: null,
    fallback_reason: null,
    input_tokens: 12,
    output_tokens: 34,
    duration_ms: 2300,
    omitted: 0,
    memory: null,
    ...over,
  };
}

function page(messages: Message[], over: Partial<ConversationPage> = {}): ConversationPage {
  return { conversation: conv(90), messages, older: null, unanswered: false, can_regenerate: true, ...over };
}

function start(over: Partial<StreamStart> = {}): StreamStart {
  return {
    conversation_id: ID(90),
    user_message: userMessage(5),
    assistant_message_id: ID(6),
    regenerate: false,
    replaces: null,
    provider_id: "den",
    provider_label: "Den",
    model: "small",
    local: true,
    omitted: 0,
    ...over,
  };
}

// ---- composing ----

test("characters are counted as the server counts them, not as UTF-16 units", () => {
  assert.equal(countChars(""), 0);
  assert.equal(countChars("abc"), 3);
  assert.equal(countChars("a\u{1f600}b"), 3);
  assert.equal("a\u{1f600}b".length, 4);
  assert.equal(countChars("\ud83d"), 1, "a stray half is one character");
  assert.equal(countChars("\ude00\ude00"), 2);
});

test("the counter stays hidden until the message is near the limit", () => {
  assert.equal(counterFor("x".repeat(89), 100), null);
  assert.deepEqual(counterFor("x".repeat(90), 100), { used: 90, limit: 100, over: false });
  assert.deepEqual(counterFor("x".repeat(100), 100), { used: 100, limit: 100, over: false });
  assert.deepEqual(counterFor("x".repeat(101), 100), { used: 101, limit: 100, over: true });
  assert.equal(counterFor("\u{1f600}".repeat(50), 100), null, "50 characters, though 100 units");
  assert.equal(counterFor("anything", Number.POSITIVE_INFINITY), null, "no limit known yet");
  assert.equal(counterText({ used: 1234, limit: 2000, over: false }, "en-US"), "1,234 / 2,000");
});

test("an empty or blank message is never sent, and nor is one over the limit", () => {
  assert.equal(canSend("", 100), false);
  assert.equal(canSend("  \n\t ", 100), false);
  assert.equal(canSend("hello", 100), true);
  assert.equal(canSend("x".repeat(100), 100), true);
  assert.equal(canSend("x".repeat(101), 100), false);
  assert.equal(canSend("\u{1f600}".repeat(60), 100), true, "60 characters fit a limit of 100");
  assert.equal(canSend("hello", Number.POSITIVE_INFINITY), true);
});

test("dictated words go in at the cursor with spaces only where needed", () => {
  assert.deepEqual(insertAtCursor("", 0, 0, "hello there"), { text: "hello there", cursor: 11 });
  assert.deepEqual(insertAtCursor("one three", 4, 4, "two"), { text: "one two three", cursor: 7 });
  assert.deepEqual(insertAtCursor("onethree", 3, 3, "two"), { text: "one two three", cursor: 7 });
  assert.deepEqual(insertAtCursor("start", 5, 5, " more "), { text: "start more", cursor: 10 });
  assert.deepEqual(insertAtCursor("line\n", 5, 5, "next"), { text: "line\nnext", cursor: 9 });
});

test("dictated words replace a selection, whichever way it was dragged", () => {
  assert.deepEqual(insertAtCursor("keep THIS please", 5, 9, "that"), { text: "keep that please", cursor: 9 });
  assert.deepEqual(insertAtCursor("keep THIS please", 9, 5, "that"), { text: "keep that please", cursor: 9 });
});

test("nothing dictated changes nothing, and a cursor past the end is the end", () => {
  assert.deepEqual(insertAtCursor("as is", 2, 2, "   "), { text: "as is", cursor: 2 });
  assert.deepEqual(insertAtCursor("abc", 99, 99, "d"), { text: "abc d", cursor: 5 });
});

test("a refusal's sentence is read from its detail, including validation lists", () => {
  assert.equal(refusalDetail({ detail: "Nope." }, 409), "Nope.");
  assert.equal(refusalDetail({ detail: [{ msg: "text is required" }, { msg: "too long" }] }, 422), "text is required; too long");
  assert.equal(refusalDetail(null, 502), "Request failed (502).");
  assert.equal(refusalDetail({ detail: "" }, 500), "Request failed (500).");
});

test("only the 409 that points at Models and roles offers the button to open it", () => {
  assert.equal(offersModelSettings(409, "No model is set up to answer here yet. Add a model under Settings, Models and roles, then send again."), true);
  assert.equal(offersModelSettings(409, "A reply is already being written in this conversation."), false);
  assert.equal(offersModelSettings(422, "Raise the limit under Settings, Models and roles."), false);
});

test("Enter sends, Shift+Enter makes a new line, and Escape stops only while a reply is streaming", () => {
  assert.equal(composerCommand({ key: "Enter" }, false), "send");
  assert.equal(composerCommand({ key: "Enter", shiftKey: true }, false), null);
  assert.equal(composerCommand({ key: "Enter", repeat: true }, false), "ignore", "one press sends once, however long it is held");
  assert.equal(composerCommand({ key: "Enter", composing: true }, false), null, "an input method is still composing");
  assert.equal(composerCommand({ key: "Escape" }, true), "stop");
  assert.equal(composerCommand({ key: "Escape" }, false), null);
  assert.equal(composerCommand({ key: "a" }, true), null);
});

test("n and / act only when focus is not in a field and no dialog is open", () => {
  const free = { typing: false, dialogOpen: false };
  assert.equal(screenCommand({ key: "n", ...free }), "new");
  assert.equal(screenCommand({ key: "N", ...free }), "new");
  assert.equal(screenCommand({ key: "/", ...free }), "search");
  assert.equal(screenCommand({ key: "n", typing: true, dialogOpen: false }), null);
  assert.equal(screenCommand({ key: "/", typing: false, dialogOpen: true }), null);
  assert.equal(screenCommand({ key: "n", metaKey: true, ...free }), null);
  assert.equal(screenCommand({ key: "n", repeat: true, ...free }), null);
  assert.equal(screenCommand({ key: "x", ...free }), null);
});

test("letters typed in a field, or in an open list that finds its items by letter, are not the screen's keys", () => {
  assert.equal(focusTakesKeys("TEXTAREA", false, null), true);
  assert.equal(focusTakesKeys("input", false, null), true);
  assert.equal(focusTakesKeys("SELECT", false, null), true);
  assert.equal(focusTakesKeys("DIV", true, null), true);
  // The model picker's open list: typing "n" there looks for a model, it does not start a conversation.
  assert.equal(focusTakesKeys("DIV", false, "listbox"), true);
  assert.equal(focusTakesKeys("DIV", false, "menu"), true);
  assert.equal(focusTakesKeys("BUTTON", false, null), false);
  assert.equal(focusTakesKeys("DIV", false, "log"), false);
  assert.equal(focusTakesKeys("BODY", false, null), false);
  assert.match(KEY_WIDGET_SELECTOR, /\[role="listbox"\]/);
});

// ---- words and times ----

test("an untitled conversation gets a placeholder", () => {
  assert.equal(conversationTitle({ title: "" }), UNTITLED);
  assert.equal(conversationTitle({ title: "   " }), UNTITLED);
  assert.equal(conversationTitle({ title: "Plans" }), "Plans");
});

test("list times are compact ages, then a date in the reader's locale", () => {
  const now = Date.UTC(2031, 5, 15, 12, 0, 0);
  assert.equal(formatListTime(now - 20_000, now), "now");
  assert.equal(formatListTime(now + 5000, now), "now", "a clock a little ahead");
  assert.equal(formatListTime(now - 14 * 60_000, now), "14 m");
  assert.equal(formatListTime(now - 6 * 3_600_000, now), "6 h");
  assert.equal(formatListTime(now - 3 * 86_400_000, now), "3 d");
  assert.equal(formatListTime(now - 10 * 86_400_000, now, "en-US", "UTC"), "Jun 5");
  assert.equal(formatListTime(now - 400 * 86_400_000, now, "en-US", "UTC"), "May 11, 2030");
});

test("durations and token counts read naturally", () => {
  assert.equal(formatDuration(850), "850 ms");
  assert.equal(formatDuration(2300, "en-US"), "2.3 s");
  assert.equal(formatDuration(65_000), "1 m 05 s");
  assert.equal(formatTokens(1234, 567, "en-US"), "1,234 in · 567 out tokens");
  assert.equal(formatTokens(null, 5, "en-US"), "5 out tokens");
  assert.equal(formatTokens(null, null), null);
});

test("a fallback always says which model was meant to answer and why it did not", () => {
  assert.equal(fallbackNote(reply(2)), null);
  assert.equal(
    fallbackNote(reply(2, { fallback_from: "den/big", fallback_reason: "It did not answer in time", model: "small" })),
    "Fallback: den/big was meant to answer but did not, so small answered instead. Why: It did not answer in time.",
  );
  assert.equal(
    fallbackNote(reply(2, { fallback_from: "den/big", fallback_reason: null, model: null })),
    "Fallback: den/big was meant to answer but did not, so another model answered instead.",
  );
});

test("the line under a reply shows model, where it ran, tokens and time, and marks a stopped one", () => {
  assert.deepEqual(replyMeta(reply(2), "en-US"), { model: "small", where: "local", tokens: "12 in · 34 out tokens", duration: "2.3 s", stopped: false, fallback: null, omitted: null });
  const stopped = replyMeta(reply(2, { state: "stopped", local: false, input_tokens: null, output_tokens: null, omitted: 3 }), "en-US");
  assert.equal(stopped.stopped, true);
  assert.equal(stopped.where, "hosted");
  assert.equal(stopped.tokens, null);
  assert.equal(stopped.omitted, "3 earlier messages were not sent to the model");
  const unreached = replyMeta(reply(2, { state: "failed", model: null, local: null, provider_id: null, duration_ms: 0 }));
  assert.deepEqual([unreached.model, unreached.where, unreached.duration], [null, null, null]);
  assert.equal(omittedNote(1), "1 earlier message was not sent to the model");
  assert.equal(omittedNote(0), null);
});

// ---- the list ----

test("search matches every typed word against the title, whatever the case", () => {
  const list = [conv(1, { title: "Garden plans" }), conv(2, { title: "Plans for the shed" }), conv(3, { title: "" })];
  assert.deepEqual(filterConversations(list, "plans").map((c) => c.id), [ID(1), ID(2)]);
  assert.deepEqual(filterConversations(list, "SHED plans").map((c) => c.id), [ID(2)]);
  assert.deepEqual(filterConversations(list, "untitled").map((c) => c.id), [ID(3)]);
  assert.equal(filterConversations(list, "   ").length, 3);
  assert.equal(filterConversations(list, "nothing").length, 0);
});

test("the list is every loaded page, each conversation once, newest activity first", () => {
  const data = { pages: [{ conversations: [conv(3), conv(2)], next: "x" }, { conversations: [conv(2), conv(1)], next: null }], pageParams: [] };
  assert.deepEqual(flattenList(data).map((c) => c.id), [ID(3), ID(2), ID(1)]);
  assert.deepEqual(flattenList(undefined), []);
});

test("a changed conversation is replaced where it is, added if new, and leaves the list it no longer belongs to", () => {
  const data = { pages: [{ conversations: [conv(3), conv(2)], next: null }], pageParams: [] };
  const renamed = placeConversation(data, conv(2, { title: "Renamed" }), false);
  assert.deepEqual(renamed?.pages[0]?.conversations.map((c) => c.title), ["Conversation 3", "Renamed"]);
  const added = placeConversation(data, conv(9), false);
  assert.deepEqual(added?.pages[0]?.conversations.map((c) => c.id), [ID(9), ID(3), ID(2)]);
  const archived = conv(3, { archived: true });
  assert.deepEqual(placeConversation(data, archived, false)?.pages[0]?.conversations.map((c) => c.id), [ID(2)]);
  const archive = { pages: [{ conversations: [], next: null }], pageParams: [] };
  assert.deepEqual(placeConversation(archive, archived, true)?.pages[0]?.conversations.map((c) => c.id), [ID(3)]);
  assert.equal(placeConversation(undefined, archived, true), undefined);
  assert.deepEqual(data.pages[0]?.conversations.map((c) => c.id), [ID(3), ID(2)], "the original is untouched");
});

// ---- the picker ----

const options: ChatOptions = {
  roles: [
    { role: "default", provider_id: "den", provider_label: "Den", model: "small", effort: null, local: true, available: true, inherits_default: false, fallbacks: 1, memory: true },
    { role: "smart", provider_id: null, provider_label: null, model: null, effort: null, local: null, available: false, inherits_default: false, fallbacks: 0, memory: false },
  ],
  providers: [{ id: "den", label: "Den", type: "openai_compatible", local: true, models: ["small", "tiny"], listed: true, memory: true }],
  efforts: ["low", "high"],
  defaults: { target: { kind: "role", role: "default" }, effort: null, local_only: false, on_record: true, use_memory: true },
  memory: { to_hosted: false },
  max_message_chars: 200000,
  model_titles: false,
  web: { enabled: false, default: false, search: false, help: "" },
};

test("the server's options become the model picker's options", () => {
  assert.deepEqual(toPickerOptions(options), {
    roles: [
      { role: "default", provider_label: "Den", model: "small", local: true, effort: null },
      { role: "smart", provider_label: null, model: null, local: null, effort: null },
    ],
    providers: [{ id: "den", label: "Den", local: true, models: ["small", "tiny"] }],
    efforts: ["low", "high"],
  });
  assert.deepEqual(toPickerOptions(undefined), { roles: [], providers: [], efforts: [] });
});

test("targets go to the picker and back, and anything unexpected reads as the default role", () => {
  assert.deepEqual(toPickerTarget({ kind: "model", provider_id: "den", model: "tiny" }), { kind: "model", provider_id: "den", model: "tiny" });
  assert.deepEqual(toPickerTarget({ kind: "role", role: "smart" }), { kind: "role", role: "smart" });
  assert.deepEqual(toPickerTarget(undefined), { kind: "role", role: "default" });
  assert.deepEqual(toPickerTarget({ kind: "agent", agent_id: "a" } as never), { kind: "role", role: "default" });
  assert.deepEqual(fromPickerTarget({ kind: "role", role: "code" }), { kind: "role", role: "code" });
  assert.deepEqual(fromPickerTarget({ kind: "model", provider_id: "den", model: "tiny" }), { kind: "model", provider_id: "den", model: "tiny" });
  assert.equal(fromPickerTarget({ kind: "role", role: "embeddings" }), null, "not a role chat can use");
});

test("a model is ready when a role is set up or a provider offers a model", () => {
  assert.equal(modelReady(options), true);
  assert.equal(modelReady({ ...options, providers: [] }), false, "no provider switched on");
  const bare = { ...options, roles: options.roles.map((role) => ({ ...role, model: null, available: false })) };
  assert.equal(modelReady(bare), true, "a model can still be picked directly");
  assert.equal(modelReady({ ...bare, providers: [{ ...options.providers[0]!, models: [] }] }), false);
});

// ---- the route ----

test("the hash sub-route names a conversation, a new one, or nothing", () => {
  assert.deepEqual(parseChatRoute(ID(1)), { kind: "conversation", id: ID(1) });
  assert.deepEqual(parseChatRoute(ID(1).toLowerCase()), { kind: "conversation", id: ID(1) });
  assert.deepEqual(parseChatRoute("new"), { kind: "new" });
  assert.deepEqual(parseChatRoute(null), { kind: "none" });
  assert.deepEqual(parseChatRoute("not-an-id"), { kind: "none" });
  assert.deepEqual(parseChatRoute("../../etc"), { kind: "none" });
});

test("only a conversation id is taken back from storage", () => {
  assert.equal(parseStoredId(ID(1)), ID(1));
  assert.equal(parseStoredId(null), null);
  assert.equal(parseStoredId("javascript:alert(1)"), undefined);
  assert.equal(parseStoredId(42), undefined);
});

// ---- the thread ----

test("loaded pages read oldest first with each message once", () => {
  const pages = [page([userMessage(3), reply(4)]), page([userMessage(1), reply(2), userMessage(3)], { older: null })];
  assert.deepEqual(flattenPages(pages).map((m) => m.id), [ID(1), ID(2), ID(3), ID(4)]);
  assert.deepEqual(flattenPages([]), []);
});

test("with nothing being written the thread is just the stored messages", () => {
  const messages = [userMessage(1), reply(2)];
  assert.deepEqual(threadView(messages, undefined), { messages, pendingText: null, writing: null });
});

test("a message just sent shows at once, before the server confirms it", () => {
  const view = threadView([userMessage(1), reply(2)], newLiveReply("next question"));
  assert.equal(view.pendingText, "next question");
  assert.equal(view.messages.length, 2);
  assert.notEqual(view.writing, null);
});

test("once the stream starts the stored message replaces the pending one and the reply grows", () => {
  const live = { ...newLiveReply("question 5"), phase: "streaming" as const, start: start(), text: "so far" };
  const view = threadView([userMessage(1), reply(2)], live);
  assert.equal(view.pendingText, null);
  assert.deepEqual(view.messages.map((m) => m.id), [ID(1), ID(2), ID(5)]);
  assert.deepEqual(view.writing, { id: ID(6), text: "so far", start: live.start });
});

test("the owner's message is not shown twice when the server's copy already has it", () => {
  const live = { ...newLiveReply("question 5"), phase: "streaming" as const, start: start() };
  const view = threadView([userMessage(1), reply(2), userMessage(5)], live);
  assert.deepEqual(view.messages.map((m) => m.id), [ID(1), ID(2), ID(5)]);
});

test("a regenerated reply hides the one it replaces, from the first moment", () => {
  const messages = [userMessage(1), reply(2)];
  const asking = threadView(messages, newLiveReply(null));
  assert.deepEqual(asking.messages.map((m) => m.id), [ID(1)]);
  assert.equal(asking.pendingText, null);
  const live = { ...newLiveReply(null), phase: "streaming" as const, start: start({ user_message: userMessage(1), regenerate: true, replaces: ID(2), assistant_message_id: ID(7) }), text: "new" };
  const view = threadView(messages, live);
  assert.deepEqual(view.messages.map((m) => m.id), [ID(1)]);
  assert.equal(view.writing?.id, ID(7));
});

test("regenerating an unanswered message hides nothing", () => {
  const view = threadView([userMessage(1)], newLiveReply(null));
  assert.deepEqual(view.messages.map((m) => m.id), [ID(1)]);
});

test("when the server's copy already holds the finished reply the live one steps aside", () => {
  const live = { ...newLiveReply("question 5"), phase: "streaming" as const, start: start(), text: "answer" };
  const view = threadView([userMessage(5), reply(6)], live);
  assert.equal(view.writing, null);
  assert.deepEqual(view.messages.map((m) => m.id), [ID(5), ID(6)]);
});

test("a finished reply is written into the newest page with the owner's message", () => {
  const data: ThreadPages = { pages: [page([userMessage(3), reply(4)], { conversation: conv(90, { streaming: true }) }), page([userMessage(1), reply(2)])], pageParams: [undefined, ID(3)] };
  const next = applyReply(data, { userMessage: userMessage(5), replaces: null, message: reply(6) });
  assert.deepEqual(next?.pages[0]?.messages.map((m) => m.id), [ID(3), ID(4), ID(5), ID(6)]);
  assert.deepEqual(next?.pages[1]?.messages.map((m) => m.id), [ID(1), ID(2)]);
  assert.equal(next?.pages[0]?.conversation.streaming, false);
  assert.equal(next?.pages[0]?.unanswered, false);
  assert.equal(next?.pages[0]?.can_regenerate, true);
  assert.equal(data.pages[0]?.messages.length, 2, "the original is untouched");
});

test("a regenerated reply takes the place of the old one", () => {
  const data: ThreadPages = { pages: [page([userMessage(1), reply(2)])], pageParams: [undefined] };
  const next = applyReply(data, { userMessage: userMessage(1), replaces: ID(2), message: reply(7, { reply_to: ID(1) }) });
  assert.deepEqual(next?.pages[0]?.messages.map((m) => m.id), [ID(1), ID(7)]);
});

test("a reply the server's copy already holds is not added twice", () => {
  const data: ThreadPages = { pages: [page([userMessage(5), reply(6, { text: "old copy" })])], pageParams: [undefined] };
  const next = applyReply(data, { userMessage: userMessage(5), replaces: null, message: reply(6, { text: "final" }) });
  assert.deepEqual(next?.pages[0]?.messages.map((m) => m.id), [ID(5), ID(6)]);
  assert.equal(next?.pages[0]?.messages[1]?.text, "final");
});

test("a reply that could not be saved leaves the owner's message unanswered", () => {
  const data: ThreadPages = { pages: [page([])], pageParams: [undefined] };
  const next = applyReply(data, { userMessage: userMessage(5), replaces: null, message: null });
  assert.deepEqual(next?.pages[0]?.messages.map((m) => m.id), [ID(5)]);
  assert.equal(next?.pages[0]?.unanswered, true);
  assert.equal(applyReply(undefined, { userMessage: null, replaces: null, message: null }), undefined);
});

test("the view follows new text only while it is near the end", () => {
  assert.equal(nearBottom(900, 100, 1000), true);
  assert.equal(nearBottom(860, 100, 1000), true);
  assert.equal(nearBottom(800, 100, 1000), false);
  assert.equal(nearBottom(0, 500, 400), true, "nothing to scroll");
});

// ---- the title search, the row's archive button, and what ends with the session ----

test("the search is sent to the server tidied, lower-cased and never longer than it takes", () => {
  assert.equal(searchQuery("  Shed   PLANS \n"), "shed plans");
  assert.equal(searchQuery("   "), "");
  assert.equal(searchQuery("x".repeat(300)).length, MAX_SEARCH);
  assert.equal(searchQuery(`${"x".repeat(119)} y`), "x".repeat(119));
});

test("a title matches a search when it holds every word, as the server decides it", () => {
  assert.equal(titleMatches("Shed and garden plans", "PLANS shed"), true);
  assert.equal(titleMatches("Shed and garden plans", "plans roof"), false);
  assert.equal(titleMatches("", ""), true);
  // The server knows an untitled conversation by its empty title, not by the label shown for it.
  assert.equal(titleMatches("", "untitled"), false);
});

test("a changed conversation enters a searched list only when its title matches the search", () => {
  const data = { pages: [{ conversations: [conv(3, { title: "Garden plans" })], next: null }], pageParams: [undefined] };
  assert.deepEqual(placeConversation(data, conv(9, { title: "Shed roof" }), false, "plans")?.pages[0]?.conversations.map((c) => c.id), [ID(3)]);
  assert.deepEqual(placeConversation(data, conv(9, { title: "Shed plans" }), false, "plans")?.pages[0]?.conversations.map((c) => c.id), [ID(9), ID(3)]);
  // Renamed so that it no longer matches: it leaves the searched list.
  assert.deepEqual(placeConversation(data, conv(3, { title: "Garden" }), false, "plans")?.pages[0]?.conversations, []);
  assert.deepEqual(placeConversation(data, conv(3, { title: "Garden" }), false)?.pages[0]?.conversations.map((c) => c.title), ["Garden"]);
});

test("each search has a list of its own, under the key every list of that kind shares", () => {
  assert.deepEqual(listKey(false), [...listsKey(false), ""]);
  assert.deepEqual(listKey(true, "plans"), [...listsKey(true), "plans"]);
  assert.notDeepEqual(listsKey(true), listsKey(false));
  assert.deepEqual(listsKey(false).slice(0, CHAT_LIST_KEY.length), [...CHAT_LIST_KEY]);
});

test("the archive button on a list row cannot be pressed while hidden and is always shown on a touch screen", () => {
  const classes = ROW_ACTION_CLASS.split(/\s+/);
  // Hidden and unpressable by default, so a tap on the row's right edge opens the conversation.
  assert.ok(classes.includes("opacity-0") && classes.includes("pointer-events-none"));
  // Wherever it becomes visible it becomes pressable too.
  for (const when of ["group-hover", "group-focus-within", "focus-visible", "[@media(hover:none)]"]) {
    assert.ok(classes.includes(`${when}:opacity-100`), `${when} shows it`);
    assert.ok(classes.includes(`${when}:pointer-events-auto`), `${when} lets it be pressed`);
  }
});

test("ending the session closes every reply being listened to and leaves them silent", () => {
  const cancelled: number[] = [];
  const run = (frame: number | null): EndableRun => ({ abort: new AbortController(), buffer: "half a sent", frame, stopped: false, dropped: false });
  const runs = new Map<string, EndableRun>([
    [ID(1), run(7)],
    [ID(2), run(null)],
  ]);
  const held = [...runs.values()];
  endRuns(runs, (frame) => cancelled.push(frame));
  assert.equal(runs.size, 0);
  assert.deepEqual(cancelled, [7]);
  for (const each of held) {
    assert.equal(each.abort.signal.aborted, true);
    // `stopped` keeps the dropped-connection toast off the sign-in screen.
    assert.deepEqual([each.stopped, each.dropped, each.frame, each.buffer], [true, true, null, ""]);
  }
});

test("drafts survive a change of screen and are cleared when the session ends", () => {
  saveDrafts({ new: "half a thought", [ID(1)]: "and another" });
  assert.deepEqual(savedDrafts(), { new: "half a thought", [ID(1)]: "and another" });
  runSessionEndHooks();
  assert.deepEqual(savedDrafts(), {});
  saveDrafts({ new: "typed after signing back in" });
  assert.deepEqual(savedDrafts(), { new: "typed after signing back in" });
  clearDrafts();
});

// ---- the effort control and the Local only switch ----

test("the effort control names the effort the chosen role is set up with", () => {
  const withEffort = toPickerOptions({ ...options, roles: [{ ...options.roles[0]!, effort: "none" }, { ...options.roles[1]!, effort: "high" }] });
  assert.equal(unsetEffortLabel({ kind: "role", role: "default" }, withEffort), "Effort: none");
  assert.equal(unsetEffortLabel({ kind: "role", role: "smart" }, withEffort), "Effort: high");
  assert.equal(unsetEffortLabel({ kind: "role", role: "code" }, withEffort), "Effort: model's own default");
  assert.equal(unsetEffortLabel({ kind: "model", provider_id: "den", model: "tiny" }, withEffort), "Effort: model's own default");
});

test("the effort control says a role has no effort of its own as the default, and stays plain when roles do not say", () => {
  assert.equal(unsetEffortLabel({ kind: "role", role: "default" }, toPickerOptions(options)), "Effort: model's own default");
  assert.equal(unsetEffortLabel({ kind: "role", role: "default" }, { roles: [{ role: "default", provider_label: null, model: null, local: null }] }), "effort");
});

test("Local only is switched off with a reason when every provider in use is local", () => {
  const toggle = localToggle(options);
  assert.equal(toggle.disabled, true);
  assert.match(toggle.reason ?? "", /already runs on your own machines/);
});

test("Local only stays usable when a hosted provider is switched on, or while nothing is known", () => {
  const hosted = { ...options.providers[0]!, id: "far", local: false };
  assert.deepEqual(localToggle({ providers: [...options.providers, hosted] }), { disabled: false, reason: null });
  assert.deepEqual(localToggle({ providers: [] }), { disabled: false, reason: null });
  assert.deepEqual(localToggle(undefined), { disabled: false, reason: null });
});
