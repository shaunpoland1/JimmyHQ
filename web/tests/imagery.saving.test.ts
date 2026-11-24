// Imagery's saving of pictures: which bytes a picture is, what a refused save is told as, and
// how "Save all starred" goes on, stops and says so. The page's pure rules only; the bridge
// and the page are not here.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { NOT_A_PICTURE, PICTURE_TYPES, saveAll, saveAllLabel, saveAllLine, saveRefusal, smallerNote, sniffType, typedPicture } from "../../apps/imagery/web/lib/saving.js";

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13];
const JPEG = [0xff, 0xd8, 0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46, 0, 1];
const WEBP = [..."RIFF"].map((c) => c.charCodeAt(0)).concat([1, 2, 3, 4], [..."WEBP"].map((c) => c.charCodeAt(0)));
const why = (error: { message?: string; owner_message?: string }) => error.owner_message ?? error.message ?? "That did not work.";

function refusal(code: string, message: string, owner_message?: string) {
  return Object.assign(new Error(message), { code, message, owner_message });
}

test("a picture's type is read from its first bytes, and only PNG, JPEG and WebP are pictures", () => {
  assert.equal(sniffType(new Uint8Array(PNG)), "image/png");
  assert.equal(sniffType(new Uint8Array(JPEG)), "image/jpeg");
  assert.equal(sniffType(new Uint8Array(WEBP)), "image/webp");
  assert.equal(sniffType(new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0, 0, 0, 0, 0, 0])), null);
  assert.equal(sniffType(new Uint8Array([60, 104, 116, 109, 108])), null);
  assert.deepEqual(PICTURE_TYPES, ["image/png", "image/jpeg", "image/webp"]);
});

test("a picture JimmyHQ drew is saved as the type its bytes say, whatever type the browser gave the Blob", async () => {
  const odd = new Blob([new Uint8Array(PNG)], { type: "application/octet-stream" });
  const typed = await typedPicture(odd, "image/webp");
  assert.equal(typed.type, "image/png");
  assert.equal(typed.size, odd.size);
  const same = new Blob([new Uint8Array(JPEG)], { type: "image/jpeg" });
  assert.equal(await typedPicture(same, "image/png"), same);
});

test("bytes that start like no picture fall back to the type Imagery stored, or are not saved at all", async () => {
  const strange = new Blob([new Uint8Array([1, 2, 3, 4])], { type: "" });
  assert.equal((await typedPicture(strange, "image/webp")).type, "image/webp");
  assert.equal(await typedPicture(strange, "text/html"), null);
  assert.equal(await typedPicture(strange, undefined), null);
  assert.equal(await typedPicture(null, "image/png"), null);
  assert.match(NOT_A_PICTURE, /nothing was saved/);
});

test("a refusal is shown with the sentence JimmyHQ wrote for the owner when it gave one", () => {
  const given = refusal("quota", "You already saved 12 files in the last minute.", "This app has already saved 12 files in the last minute. Wait a minute and press the button again.");
  assert.equal(saveRefusal(given, why), "This app has already saved 12 files in the last minute. Wait a minute and press the button again.");
  assert.equal(saveRefusal(refusal("not_allowed", "No touch."), why), "No touch.");
});

test("a picture too large to save is told in Imagery's own words naming the setting, since JimmyHQ's is for an app's author", () => {
  const said = saveRefusal(refusal("too_large", "That file is larger than the 8 MB an app may save. Split it into parts."), why);
  assert.match(said, /Largest file an app may offer you to keep \(MB\)/);
  assert.match(said, /not saved/);
  assert.doesNotMatch(said, /Split it/);
  assert.equal(saveRefusal(refusal("too_large", "x", "Raise it under Settings, Apps."), why), "Raise it under Settings, Apps.");
});

test("a picture drawn smaller than the one kept says how to see it larger, and a whole one says nothing", () => {
  const row = { width: 4096, height: 2731 };
  assert.match(smallerNote(row, 2048, 1366), /2048 by 1366 pixels, smaller than the 4096 by 2731 Imagery keeps/);
  assert.match(smallerNote(row, 2048, 1366), /Longest side of a picture shown in full \(pixels\)/);
  assert.equal(smallerNote(row, 4096, 2731), "");
  assert.equal(smallerNote({ width: null, height: null }, 100, 100), "");
  assert.equal(smallerNote(row, 0, 0), "");
});

// --- Save all starred -----------------------------------------------------------------

const rows = (n: number) => Array.from({ length: n }, (_, i) => ({ id: i + 1 }));

/** A fake save that JimmyHQ stops after `limit` files, as it does for one click. */
function fakes(limit: number, over = refusal("quota", "More than the files one click may save.", "That was more than the 5 files one click may save, so it was not saved. Press the button again for the rest.")) {
  const saved: number[] = [];
  let pictures = 0;
  return {
    saved,
    pictures: () => pictures,
    io: {
      picture: async (row: { id: number }) => { pictures += 1; return { id: row.id }; },
      save: async (row: { id: number }) => { if (saved.length >= limit) throw over; saved.push(row.id); return { name: `p${row.id}.png` }; },
      sentence: (error: { message: string; owner_message?: string }, stage: string) => `[${stage}] ${why(error)}`,
    },
  };
}

test("every starred picture is saved one after another when JimmyHQ allows them all", async () => {
  const run = fakes(10);
  const result = await saveAll(rows(4), run.io);
  assert.deepEqual(result.saved, [1, 2, 3, 4]);
  assert.deepEqual(result.left, []);
  assert.equal(result.stopped, null);
  assert.equal(saveAllLine(result), "Saved all 4 pictures.");
});

test("when JimmyHQ stops the run at its per-click limit nothing more is tried and the owner is told how many are left", async () => {
  const run = fakes(5);
  const result = await saveAll(rows(8), run.io);
  assert.deepEqual(result.saved, [1, 2, 3, 4, 5]);
  assert.deepEqual(result.left, [6, 7, 8]);
  assert.equal(result.stopped?.code, "quota");
  assert.equal(run.pictures(), 6);
  const line = saveAllLine(result);
  assert.match(line, /^Saved 5 of 8, then stopped\./);
  assert.match(line, /\[save\] That was more than the 5 files one click may save/);
  assert.match(line, /3 starred pictures are not saved yet\. Press the button again for them\./);
});

test("the next press goes on with the pictures an earlier run left and saves none twice", async () => {
  const first = await saveAll(rows(8), fakes(5).io);
  const done = new Set(first.saved);
  const second = fakes(5);
  const result = await saveAll(rows(8), second.io, done);
  assert.deepEqual(second.saved, [6, 7, 8]);
  assert.deepEqual(result.left, []);
  assert.equal(result.pending, 3);
  assert.equal(saveAllLine(result), "Saved all 3 pictures.");
});

test("a run stopped by the per-minute limit says so in JimmyHQ's own words and saves nothing more", async () => {
  const minute = refusal("quota", "x", "This app has already saved 12 files in the last minute, the most it may, so that one was not saved. Wait a minute and press the button again.");
  const result = await saveAll(rows(3), fakes(0, minute).io);
  assert.equal(result.saved.length, 0);
  assert.equal(saveAllLine(result), "Nothing was saved. [save] This app has already saved 12 files in the last minute, the most it may, so that one was not saved. Wait a minute and press the button again. 3 starred pictures are not saved yet. Press the button again for them.");
});

test("a click that went stale stops the run, since only the owner's next click may save more", async () => {
  const stale = refusal("not_allowed", "x", "That took too long, or the click was not in the app, so the file was not saved. Press the button again.");
  const result = await saveAll(rows(3), fakes(1, stale).io);
  assert.deepEqual(result.saved, [1]);
  assert.equal(result.stopped?.code, "not_allowed");
  assert.match(saveAllLine(result), /Saved 1 of 3, then stopped\. \[save\] That took too long/);
});

test("a picture that is too large to show or to save is left out and the others still go on", async () => {
  const run = fakes(10);
  const heavy = refusal("too_large", "x", "This picture is larger than the 25 MB an app may show in full.");
  const io = { ...run.io, picture: async (row: { id: number }) => { if (row.id === 2) throw heavy; return { id: row.id }; } };
  const result = await saveAll(rows(3), io);
  assert.deepEqual(result.saved, [1, 3]);
  assert.deepEqual(result.skipped, [{ id: 2, said: "[picture] This picture is larger than the 25 MB an app may show in full." }]);
  assert.deepEqual(result.left, []);
  assert.equal(result.stopped, null);
  assert.match(saveAllLine(result), /^Saved 2 pictures\. One was left out: \[picture\] This picture is larger/);
});

test("a refusal to show a picture, such as too many full views a minute, stops the run like any other", async () => {
  const run = fakes(10);
  const often = refusal("quota", "x", "You asked for more full-size pictures in a minute than JimmyHQ allows.");
  const io = { ...run.io, picture: async (row: { id: number }) => { if (row.id === 3) throw often; return { id: row.id }; } };
  const result = await saveAll(rows(5), io);
  assert.deepEqual(result.saved, [1, 2]);
  assert.deepEqual(result.left, [3, 4, 5]);
  assert.match(saveAllLine(result), /\[picture\] You asked for more full-size pictures/);
});

test("with nothing starred left to save the run says so and the button offers all of them again", async () => {
  const result = await saveAll(rows(2), fakes(10).io, new Set([1, 2]));
  assert.equal(result.pending, 0);
  assert.equal(saveAllLine(result), "There is no starred picture left to save.");
  assert.equal(saveAllLabel(7, 0), "Save all starred (7)");
  assert.equal(saveAllLabel(7, 3), "Save the other 3 starred");
});

test("the saving code never builds markup from text, and never uses the browser's own dialogs", () => {
  for (const path of ["lib/saving.js", "views/saving.js", "views/detail.js", "views/gallery.js"]) {
    const text = readFileSync(`../apps/imagery/web/${path}`, "utf8");
    assert.doesNotMatch(text, /\b(alert|confirm|prompt)\s*\(/, path);
    assert.doesNotMatch(text, /innerHTML|insertAdjacentHTML/, path);
  }
});

test("every object address the large view makes is revoked when its window closes", () => {
  const text = readFileSync("../apps/imagery/web/views/detail.js", "utf8");
  assert.equal((text.match(/URL\.createObjectURL/g) ?? []).length, 1);
  assert.match(text, /URL\.revokeObjectURL\(url\)/);
  assert.match(text, /close\(\) \{\s*closed = true;\s*forget\(\);/);
  assert.match(text, /\.then\(\(\) => view\.close\(\)\)/);
  assert.match(text, /function show\(blob\) \{\s*forget\(\);/);
});
