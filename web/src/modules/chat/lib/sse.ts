// A parser for server-sent events read from a fetch response body. The
// browser's EventSource cannot POST, so the chat stream is parsed here.
//
// Follows the event-stream format: lines end with LF, CRLF or a lone CR; a
// line starting with ":" is a comment; "field: value" drops one leading
// space; a blank line dispatches the event; several data lines are joined
// with newlines; an event with no data is not dispatched; an event the stream
// ends in the middle of is dropped.

export type SseMessage = { event: string; data: string };

export type SseParser = {
  /** Takes the next piece of text and returns the events it completed. */
  feed: (chunk: string) => SseMessage[];
  /** The stream is over. Returns anything a held line ending completed. */
  end: () => SseMessage[];
};

const BOM = 0xfeff;

export function createSseParser(): SseParser {
  let buffer = "";
  /** How far into `buffer` has already been searched for a line ending. */
  let searched = 0;
  let started = false;
  let event = "";
  let data: string[] = [];

  const takeLine = (line: string, out: SseMessage[]) => {
    if (line === "") {
      if (data.length > 0) out.push({ event: event || "message", data: data.join("\n") });
      event = "";
      data = [];
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    // "id" and "retry" mean nothing on a one-off POST stream.
  };

  const drain = (atEnd: boolean): SseMessage[] => {
    const out: SseMessage[] = [];
    let start = 0;
    let at = searched;
    while (at < buffer.length) {
      const code = buffer.charCodeAt(at);
      if (code !== 10 && code !== 13) {
        at += 1;
        continue;
      }
      // A CR at the very end may be the first half of a CRLF still to come.
      if (code === 13 && at === buffer.length - 1 && !atEnd) break;
      takeLine(buffer.slice(start, at), out);
      at += code === 13 && buffer.charCodeAt(at + 1) === 10 ? 2 : 1;
      start = at;
    }
    buffer = buffer.slice(start);
    searched = Math.max(0, at - start);
    return out;
  };

  return {
    feed(chunk) {
      if (chunk === "") return [];
      if (!started) {
        started = true;
        if (chunk.charCodeAt(0) === BOM) chunk = chunk.slice(1);
      }
      buffer += chunk;
      return drain(false);
    },
    end() {
      const out = drain(true);
      // Whatever is left is a line, and an event, that never finished.
      buffer = "";
      searched = 0;
      event = "";
      data = [];
      return out;
    },
  };
}

/**
 * Reads a response body as server-sent events. Bytes are decoded as UTF-8
 * across chunk boundaries. Stopping early (a `break`) cancels the stream.
 */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseMessage, void, undefined> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = createSseParser();
  let finished = false;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) yield* parser.feed(decoder.decode(value, { stream: true }));
    }
    finished = true;
    yield* parser.feed(decoder.decode());
    yield* parser.end();
  } finally {
    if (!finished) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
