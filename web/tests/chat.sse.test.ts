import assert from "node:assert/strict";
import { test } from "node:test";
import { interpretReplyEvent } from "../src/modules/chat/lib/reply.ts";
import { createSseParser, readSse } from "../src/modules/chat/lib/sse.ts";
import type { SseMessage } from "../src/modules/chat/lib/sse.ts";

function parseAll(chunks: string[]): SseMessage[] {
  const parser = createSseParser();
  const out: SseMessage[] = [];
  for (const chunk of chunks) out.push(...parser.feed(chunk));
  out.push(...parser.end());
  return out;
}

function bodyOf(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
}

test("an event is its name and its data, dispatched by the blank line", () => {
  assert.deepEqual(parseAll(['event: delta\ndata: {"text":"hi"}\n\n']), [{ event: "delta", data: '{"text":"hi"}' }]);
});

test("an event with no name is a message", () => {
  assert.deepEqual(parseAll(["data: plain\n\n"]), [{ event: "message", data: "plain" }]);
});

test("an event split across chunks at any point comes out whole", () => {
  const whole = 'event: start\ndata: {"a":1}\n\nevent: delta\ndata: {"text":"one two"}\n\n';
  const expected = parseAll([whole]);
  assert.equal(expected.length, 2);
  for (let cut = 1; cut < whole.length; cut += 1) {
    assert.deepEqual(parseAll([whole.slice(0, cut), whole.slice(cut)]), expected, `cut at ${cut}`);
  }
  assert.deepEqual(parseAll([...whole]), expected, "one character at a time");
});

test("several data lines are joined with newlines", () => {
  assert.deepEqual(parseAll(["data: first\ndata: second\ndata:\ndata: fourth\n\n"]), [{ event: "message", data: "first\nsecond\n\nfourth" }]);
});

test("CRLF and lone CR line endings work, even when a CRLF is split between chunks", () => {
  const expected = [
    { event: "delta", data: "a" },
    { event: "delta", data: "b" },
  ];
  assert.deepEqual(parseAll(["event: delta\r\ndata: a\r\n\r\nevent: delta\r\ndata: b\r\n\r\n"]), expected);
  assert.deepEqual(parseAll(["event: delta\rdata: a\r\revent: delta\rdata: b\r\r"]), expected);
  assert.deepEqual(parseAll(["event: delta\r", "\ndata: a\r", "\n\r", "\nevent: delta\r\ndata: b\r\n\r", "\n"]), expected);
});

test("a blank line made of a split CRLF dispatches once, not twice", () => {
  const parser = createSseParser();
  assert.deepEqual(parser.feed("data: x\r\n\r"), []);
  assert.deepEqual(parser.feed("\ndata: y\r\n\r\n"), [
    { event: "message", data: "x" },
    { event: "message", data: "y" },
  ]);
});

test("comment lines are keep-alives and produce nothing", () => {
  assert.deepEqual(parseAll([": keep-alive\n\n", "event: delta\n: in the middle\ndata: x\n\n", ":\n\n"]), [{ event: "delta", data: "x" }]);
});

test("only one space after the colon is dropped, and a line with no colon is a field with no value", () => {
  assert.deepEqual(parseAll(["data:  two spaces\n\n", "data:none\n\n", "data\n\n"]), [
    { event: "message", data: " two spaces" },
    { event: "message", data: "none" },
    { event: "message", data: "" },
  ]);
});

test("an event name with no data is not dispatched and does not leak into the next event", () => {
  assert.deepEqual(parseAll(["event: orphan\n\n", "data: x\n\n"]), [{ event: "message", data: "x" }]);
});

test("unknown fields, id and retry are ignored", () => {
  assert.deepEqual(parseAll(["id: 7\nretry: 100\nwhatever: yes\nevent: done\ndata: {}\n\n"]), [{ event: "done", data: "{}" }]);
});

test("a stream that ends in the middle of an event drops the unfinished event", () => {
  assert.deepEqual(parseAll(['event: delta\ndata: {"text":"a"}\n\nevent: delta\ndata: {"te']), [{ event: "delta", data: '{"text":"a"}' }]);
  assert.deepEqual(parseAll(['event: delta\ndata: {"text":"b"}\n']), []);
});

test("a stream that ends on a held CR still finishes that line", () => {
  assert.deepEqual(parseAll(["data: x\r\n\r"]), [{ event: "message", data: "x" }]);
});

test("a byte order mark at the very start is skipped", () => {
  assert.deepEqual(parseAll(["﻿data: x\n\n"]), [{ event: "message", data: "x" }]);
});

test("the parser can be used again after the stream ends", () => {
  const parser = createSseParser();
  parser.feed("event: delta\ndata: half");
  parser.end();
  assert.deepEqual(parser.feed("data: fresh\n\n"), [{ event: "message", data: "fresh" }]);
});

test("a long data line arriving in many pieces is read once, whole", () => {
  const long = "x".repeat(50_000);
  const pieces = [`data: ${long.slice(0, 10_000)}`, long.slice(10_000, 30_000), long.slice(30_000), "\n", "\n"];
  assert.deepEqual(parseAll(pieces), [{ event: "message", data: long }]);
});

test("bytes are decoded as UTF-8 even when a character is split between chunks", async () => {
  const bytes = new TextEncoder().encode('event: delta\ndata: {"text":"café \u{1f600}"}\n\n');
  for (let cut = 1; cut < bytes.length; cut += 1) {
    const got: SseMessage[] = [];
    for await (const message of readSse(bodyOf([bytes.slice(0, cut), bytes.slice(cut)]))) got.push(message);
    assert.deepEqual(got, [{ event: "delta", data: '{"text":"café \u{1f600}"}' }], `cut at ${cut}`);
  }
});

test("stopping early cancels the response body", async () => {
  let cancelled = false;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      controller.enqueue(new TextEncoder().encode("data: again\n\n"));
    },
    cancel() {
      cancelled = true;
    },
  });
  let seen = 0;
  for await (const _message of readSse(body)) {
    seen += 1;
    if (seen === 3) break;
  }
  assert.equal(seen, 3);
  assert.equal(cancelled, true);
});

test("a body read to its end is never cancelled, so a finished reply is not reported as a failed request", async () => {
  let cancelled = false;
  const chunks = ['event: done\ndata: {"message":1}\n\n'];
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const next = chunks.shift();
      if (next) controller.enqueue(new TextEncoder().encode(next));
      else controller.close();
    },
    cancel() {
      cancelled = true;
    },
  });
  let ended = false;
  for await (const message of readSse(body)) {
    // What the reply's listener does: after the last event it keeps reading until the stream closes.
    if (ended) continue;
    ended = message.event === "done";
  }
  assert.equal(ended, true);
  assert.equal(cancelled, false);
});

test("a body that breaks part-way gives what arrived, then the error", async () => {
  let step = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      step += 1;
      if (step === 1) controller.enqueue(new TextEncoder().encode("data: one\n\ndata: tw"));
      else controller.error(new Error("connection lost"));
    },
  });
  const got: string[] = [];
  await assert.rejects(async () => {
    for await (const message of readSse(body)) got.push(message.data);
  }, /connection lost/);
  assert.deepEqual(got, ["one"]);
});

// ---- what the chat makes of each event ----

const user = { id: "01JABCDEFGHJKMNPQRSTVWXYZ0", role: "user", created: 1, text: "hello" };
const reply = { id: "01JABCDEFGHJKMNPQRSTVWXYZ1", role: "assistant", created: 2, text: "hi", state: "complete" };

test("a start event carries the stored message and the id of the reply to come", () => {
  const data = { conversation_id: "c", user_message: user, assistant_message_id: reply.id, regenerate: false, replaces: null, provider_id: "den", provider_label: "Den", model: "small", local: true, omitted: 0 };
  const event = interpretReplyEvent({ event: "start", data: JSON.stringify(data) });
  assert.equal(event?.type, "start");
  assert.deepEqual(event?.type === "start" ? event.start : null, data);
});

test("a delta carries text, and one without text is ignored", () => {
  assert.deepEqual(interpretReplyEvent({ event: "delta", data: '{"text":"a\\nb"}' }), { type: "delta", text: "a\nb" });
  assert.equal(interpretReplyEvent({ event: "delta", data: '{"text":5}' }), null);
});

test("done carries the stored reply", () => {
  assert.deepEqual(interpretReplyEvent({ event: "done", data: JSON.stringify({ message: reply }) }), { type: "done", message: reply });
  assert.equal(interpretReplyEvent({ event: "done", data: '{"message":null}' }), null);
});

test("an error carries the server's sentence, with or without a saved reply", () => {
  const failed = { ...reply, state: "failed", error: "The model did not answer." };
  assert.deepEqual(interpretReplyEvent({ event: "error", data: JSON.stringify({ detail: "Longer words.", message: failed }) }), { type: "error", detail: "Longer words.", message: failed });
  assert.deepEqual(interpretReplyEvent({ event: "error", data: '{"detail":"Not saved.","message":null}' }), { type: "error", detail: "Not saved.", message: null });
  assert.deepEqual(interpretReplyEvent({ event: "error", data: "{}" }), { type: "error", detail: "The reply failed.", message: null });
});

test("unreadable data, a malformed start and unknown events are ignored rather than thrown", () => {
  assert.equal(interpretReplyEvent({ event: "delta", data: "{not json" }), null);
  assert.equal(interpretReplyEvent({ event: "delta", data: '"text"' }), null);
  assert.equal(interpretReplyEvent({ event: "delta", data: "[1]" }), null);
  assert.equal(interpretReplyEvent({ event: "start", data: '{"assistant_message_id":"x"}' }), null);
  assert.equal(interpretReplyEvent({ event: "ping", data: "{}" }), null);
  assert.equal(interpretReplyEvent({ event: "message", data: '{"text":"x"}' }), null);
});
