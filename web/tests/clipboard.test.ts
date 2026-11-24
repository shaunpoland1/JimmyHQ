import assert from "node:assert/strict";
import { test } from "node:test";
import { copyText } from "../src/lib/clipboard.ts";
import type { ClipboardEnv } from "../src/lib/clipboard.ts";

function env(over: Partial<ClipboardEnv> & { log?: string[] } = {}): ClipboardEnv {
  const log = over.log ?? [];
  return {
    clipboard: { writeText: async (text) => void log.push(`api:${text}`) },
    copyBySelecting: (text) => {
      log.push(`select:${text}`);
      return true;
    },
    ...over,
  };
}

test("text is copied with the clipboard API when the browser lets it, and nothing else is tried", async () => {
  const log: string[] = [];
  assert.equal(await copyText("secret", env({ log })), true);
  assert.deepEqual(log, ["api:secret"]);
});

test("when the clipboard API refuses, as in a page that is not focused, the text is copied by selecting it", async () => {
  const log: string[] = [];
  const refusing = { writeText: async () => Promise.reject(new DOMException("Document is not focused.", "NotAllowedError")) };
  assert.equal(await copyText("secret", env({ log, clipboard: refusing })), true);
  assert.deepEqual(log, ["select:secret"]);
});

test("where the browser has no clipboard API, as on a plain-http address, the text is copied by selecting it", async () => {
  const log: string[] = [];
  assert.equal(await copyText("secret", env({ log, clipboard: undefined })), true);
  assert.deepEqual(log, ["select:secret"]);
});

test("when both ways fail the answer is false and nothing is thrown, so the owner is told to copy it by hand", async () => {
  const refusing = { writeText: async () => Promise.reject(new Error("no")) };
  assert.equal(await copyText("x", env({ clipboard: refusing, copyBySelecting: () => false })), false);
  assert.equal(
    await copyText("x", env({ clipboard: undefined, copyBySelecting: () => { throw new Error("no"); } })),
    false,
  );
});
