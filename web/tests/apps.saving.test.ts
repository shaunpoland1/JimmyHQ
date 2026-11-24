// `hq.ui.save`: a page hands the owner a file (text, a web page, a picture's bytes). The
// shell makes the Blob and the save; these tests try what a page could do with it.

import assert from "node:assert/strict";
import { test } from "node:test";
import { CALLS, createRelay } from "../src/modules/apps/lib/bridge.ts";
import type { BridgeResponse } from "../src/modules/apps/lib/bridge.ts";
import {
  DEFAULT_SAVE_LIMITS,
  HTML_POLICY,
  SAVE_DEFAULT_MAX_BYTES,
  SAVE_MAX_PARTS,
  SAVE_MINUTE_MS,
  SAVE_NAME_MAX,
  SAVE_OWNER_REFUSALS,
  SAVE_REFUSALS,
  SAVE_RUN_MS,
  SAVE_WINDOW_MS,
  deliver,
  htmlWithPolicy,
  looksLike,
  newLedger,
  newRun,
  noteSaved,
  pictureType,
  readSaveArgs,
  recordSave,
  saveAllowed,
  saveRunAllowed,
  saveName,
  saveType,
  savedLine,
  savesInMinute,
  shownName,
  sizeWords,
} from "../src/modules/apps/lib/saving.ts";
import type { SaveEnv, SaveLimits, SaveRun } from "../src/modules/apps/lib/saving.ts";
import { readMount } from "../src/modules/apps/lib/bridge.ts";
import { ACTIVATION_MS, createTouch, focusEntered, hint, observe, shellInput } from "../src/modules/apps/lib/touch.ts";
import { dictateRefusal } from "../src/modules/apps/lib/dictate.ts";

const TOUCH = { isActive: true, hasBeenActive: true };
const IDLE = { isActive: false, hasBeenActive: true };
// A touch just now: the time of the owner's touch, for a save made at T.
const T = 100_000;

const NO_RUN = newRun();
const MAX = DEFAULT_SAVE_LIMITS.maxBytes;

function ok(args: unknown, limits: SaveLimits = DEFAULT_SAVE_LIMITS) {
  const read = readSaveArgs(args, T, NO_RUN, T, limits);
  assert.equal(read.ok, true, JSON.stringify(read));
  return read.ok ? read.save : (undefined as never);
}

/** The bytes of a tiny picture of each kind: just the start the shell checks. */
const PNG = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);
const JPEG = Uint8Array.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3]);
const WEBP = Uint8Array.from([...Buffer.from("RIFF"), 4, 0, 0, 0, ...Buffer.from("WEBP"), 1]);

test("saving a file is a call the shell answers itself, never one the server sees", () => {
  assert.equal(CALLS["ui.save"]?.shell, true);
  assert.equal(CALLS["ui.save"]?.needs, undefined);
});

test("a page that asks to save HTML gets a plain text file when the owner has turned web pages off", () => {
  const off: SaveLimits = { ...DEFAULT_SAVE_LIMITS, html: false };
  assert.equal(saveType("text/html", false), "text/plain");
  assert.equal(saveType("application/xhtml+xml", true), "text/plain");
  assert.equal(saveType("image/svg+xml", true), "text/plain");
  const page = ok({ text: "<script>fetch('https://example.com/x')</script>", name: "report", type: "text/html" }, off);
  assert.equal(page.name, "report.txt");
  assert.equal(page.type, "text/plain");
  assert.equal(ok({ text: "<p>x</p>", name: "evil.html", type: "text/html" }, off).name, "evil.html.txt");
});

test("a page that asks to save HTML gets a .html file, and other web-like types stay plain text", () => {
  assert.equal(saveType("text/html", true), "text/html");
  assert.equal(saveType("TEXT/HTML; charset=utf-8", true), "text/html");
  const page = ok({ text: "<p>hi</p>", name: "report", type: "text/html" });
  assert.deepEqual([page.name, page.type], ["report.html", "text/html"]);
  assert.equal(ok({ text: "<p>x</p>", name: "page.htm", type: "text/html" }).name, "page.html");
  assert.equal(ok({ text: "<p>x</p>", name: "page.HTML", type: "text/html" }).name, "page.html");
  assert.equal(ok({ text: "<svg onload=x>", name: "x", type: "image/svg+xml" }).name, "x.txt");
  assert.equal(ok({ text: "<p>x</p>", name: "x", type: "application/xhtml+xml" }).name, "x.txt");
});

test("a saved web page starts with a policy that no script, form or outside load can get past", () => {
  const out = htmlWithPolicy("<!doctype html><title>T</title><script>fetch('https://example.com')</script>");
  assert.ok(out.startsWith(`<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${HTML_POLICY}">`));
  assert.match(HTML_POLICY, /^default-src 'none';/);
  assert.match(HTML_POLICY, /form-action 'none'/);
  assert.match(HTML_POLICY, /base-uri 'none'/);
  for (const open of ["script-src", "connect-src", "frame-src", "object-src", "https:", "http:", "'unsafe-eval'"]) assert.ok(!HTML_POLICY.includes(open), open);
  // The page's own words follow, unchanged.
  assert.ok(out.endsWith("<title>T</title><script>fetch('https://example.com')</script>"));
});

test("a saved web page cannot hide a refresh, a prefetch or a second policy in a meta or link tag, whatever its case", () => {
  const hostile = [
    '<meta http-equiv="refresh" content="0;url=https://example.com/?d=1">',
    "<META HTTP-EQUIV=refresh CONTENT='0;url=https://example.com'>",
    '<meta\nhttp-equiv=refresh content="0;url=https://example.com">',
    '<meta/http-equiv="refresh" content="0;url=https://example.com">',
    '<meta http-equiv="&#114;efresh" content="0;url=https://example.com">',
    '<Link rel="dns-prefetch" href="//data.example.com">',
    "<noscript><meta http-equiv=refresh content='0;url=https://example.com'></noscript>",
  ].join("");
  const out = htmlWithPolicy(hostile);
  const body = out.slice(out.indexOf(hostile.slice(0, 5).replace("<meta", "<x-meta")));
  assert.equal(/<(meta|link)/i.test(body), false, body);
  // Only the two tags JimmyHQ wrote itself are left.
  assert.equal((out.match(/<meta/gi) ?? []).length, 2);
  assert.equal((out.match(/<link/gi) ?? []).length, 0);
  assert.equal((out.match(/<x-meta/g) ?? []).length, 5);
});

test("a page that sends HTML with no doctype or a doctype of its own still gets the policy first", () => {
  for (const text of ["<p>x</p>", "   <!DOCTYPE html PUBLIC '-//W3C//DTD HTML 4.01//EN'><p>x</p>", "<!-->\u003cscript>1</script>"]) {
    const out = htmlWithPolicy(text);
    assert.ok(out.startsWith("<!doctype html><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\""), out);
    assert.equal(out.indexOf("Content-Security-Policy"), out.lastIndexOf("Content-Security-Policy"));
    assert.ok(out.indexOf("Content-Security-Policy") < out.indexOf("<p>") || !text.includes("<p>"));
  }
});

test("a type on the list keeps its ending and anything else is saved as plain text", () => {
  assert.equal(saveType("text/markdown"), "text/markdown");
  assert.equal(saveType("Text/CSV; charset=utf-8"), "text/csv");
  assert.equal(saveType("application/json"), "application/json");
  for (const other of ["application/x-msdownload", "image/svg+xml", "text/javascript", "application/pdf", "", 5, null, undefined, "text/plain/../x"]) {
    assert.equal(saveType(other), "text/plain", String(other));
  }
  assert.equal(ok({ text: "a", name: "x", type: "application/x-sh" }).name, "x.txt");
  assert.equal(ok({ text: "# a", name: "x", type: "text/markdown" }).name, "x.md");
});

test("a file name has no path, no control characters and an ending the type chose", () => {
  assert.equal(saveName("My article", "text/markdown"), "My article.md");
  assert.equal(saveName("My article.md", "text/markdown"), "My article.md");
  assert.equal(saveName("notes.MD", "text/markdown"), "notes.md");
  assert.equal(saveName("../../etc/passwd", "text/plain"), "etc-passwd.txt");
  assert.equal(saveName("C:\\Windows\\evil.exe", "text/plain"), "C-Windows-evil.exe.txt");
  assert.equal(saveName("a/b\\c", "text/csv"), "a-b-c.csv");
  assert.equal(saveName("tab\tnew\nline\u0000nul", "text/plain"), "tab-new-line-nul.txt");
  assert.equal(saveName("fake\u202egpj.exe", "text/plain"), "fake-gpj.exe.txt");
  assert.equal(saveName(".hidden", "text/plain"), "hidden.txt");
  assert.equal(saveName("trailing. . ", "text/plain"), "trailing.txt");
  assert.equal(saveName("what? <this> | *that*", "text/plain"), "what- -this- - -that.txt");
  assert.equal(saveName("evil.html", "text/plain"), "evil.html.txt");
});

test("a name that is empty, not a string or a device name still becomes a safe file name", () => {
  for (const bad of ["", "   ", "...", "///", null, undefined, 5, {}, ["a"]]) assert.equal(saveName(bad, "text/plain"), "file.txt", String(bad));
  assert.equal(saveName("CON", "text/plain"), "file-CON.txt");
  assert.equal(saveName("nul.txt", "text/plain"), "file-nul.txt");
  assert.equal(saveName("com1.backup", "text/plain"), "file-com1.backup.txt");
  assert.equal(saveName("console", "text/plain"), "console.txt");
});

test("a device name with spaces or dots after it, or a superscript digit, is still taken for the device and renamed", () => {
  assert.equal(saveName("nul .txt", "text/plain"), "file-nul.txt");
  assert.equal(saveName("CON .backup", "text/plain"), "file-CON .backup.txt");
  assert.equal(saveName("aux. .log", "text/plain"), "file-aux. .log.txt");
  assert.equal(saveName("COM¹", "text/plain"), "file-COM¹.txt");
  assert.equal(saveName("lpt³.csv", "text/csv"), "file-lpt³.csv");
  assert.equal(saveName("CONIN$", "text/plain"), "file-CONIN$.txt");
  assert.equal(saveName("COM10", "text/plain"), "COM10.txt");
});

test("no invisible or direction-changing character survives in a file name", () => {
  // The Arabic letter mark, the invisible operators, the soft hyphen, the Mongolian vowel
  // separator, a Hangul filler, a variation selector, line and paragraph separators.
  for (const hidden of ["\u061c", "\u2060", "\u2061", "\u2062", "\u2063", "\u2064", "\u00ad", "\u180e", "\u3164", "\ufe0f", "\u2028", "\u2029", "\u200b", "\u2066", "\u202e", "\ufeff"]) {
    const name = saveName(`a${hidden}txt.exe`, "text/plain");
    assert.equal(name, "a-txt.exe.txt", JSON.stringify(hidden));
    assert.ok(!/[\p{Cf}\p{Cc}\p{Default_Ignorable_Code_Point}]/u.test(name));
  }
});

test("a very long name is cut before its ending", () => {
  const name = saveName("x".repeat(5000), "application/json");
  assert.equal(name, `${"x".repeat(SAVE_NAME_MAX)}.json`);
  assert.ok(Array.from(saveName("é".repeat(500), "text/plain")).length <= SAVE_NAME_MAX + 4);
});

test("only name, type and text are taken, and the text must be a string", () => {
  for (const args of [null, "text", 5, [], { text: 5 }, { name: "x" }, { text: "a", url: "https://example.com/x" }, { text: "a", href: "blob:x" }, { text: "a", download: true }]) {
    const read = readSaveArgs(args, 0, NO_RUN, 0);
    assert.equal(read.ok, false, JSON.stringify(args));
    assert.equal(!read.ok && read.error.code, "invalid");
  }
  const empty = readSaveArgs({ text: "" }, 0, NO_RUN, 0);
  assert.equal(!empty.ok && empty.error.message, SAVE_REFUSALS.noText);
});

test("text over the limit is refused, counted in bytes, and a file at the limit is saved", () => {
  const small: SaveLimits = { ...DEFAULT_SAVE_LIMITS, maxBytes: 1_000_000 };
  const over = readSaveArgs({ text: "x".repeat(small.maxBytes + 1) }, 0, NO_RUN, 0, small);
  assert.equal(!over.ok && over.error.code, "too_large");
  // Two bytes each: half as many characters is still over.
  const wide = readSaveArgs({ text: "é".repeat(small.maxBytes / 2 + 1) }, 0, NO_RUN, 0, small);
  assert.equal(!wide.ok && wide.error.code, "too_large");
  const exact = readSaveArgs({ text: "x".repeat(small.maxBytes) }, 0, NO_RUN, 0, small);
  assert.equal(exact.ok && exact.save.bytes, small.maxBytes);
});

test("the largest file is the owner's setting: 8 MB unless they say otherwise, and a refusal says how large", () => {
  assert.equal(SAVE_DEFAULT_MAX_BYTES, 8 * 1024 * 1024);
  assert.equal(readSaveArgs({ text: "x".repeat(2_000_000) }, 0, NO_RUN, 0).ok, true); // over the old 1 MB
  const over = readSaveArgs({ text: "x".repeat(MAX + 1) }, 0, NO_RUN, 0);
  assert.equal(!over.ok && over.error.code, "too_large");
  assert.match(!over.ok ? over.error.message : "", /larger than the 8 MB an app may save/);
  const tight: SaveLimits = { ...DEFAULT_SAVE_LIMITS, maxBytes: 1024 * 1024 };
  assert.equal(readSaveArgs({ text: "x".repeat(1024 * 1024 + 1) }, 0, NO_RUN, 0, tight).ok, false);
  assert.equal(readSaveArgs({ text: "x".repeat(1024 * 1024) }, 0, NO_RUN, 0, tight).ok, true);
  assert.equal(sizeWords(8 * 1024 * 1024), "8 MB");
  assert.equal(sizeWords(1.5 * 1024 * 1024), "1.5 MB");
  assert.equal(sizeWords(300 * 1024), "300 KB");
});

test("bytes over the limit are refused before they are copied, and a picture at the limit is saved", () => {
  const tight: SaveLimits = { ...DEFAULT_SAVE_LIMITS, maxBytes: 64 };
  const big = new Uint8Array(65);
  big.set(PNG);
  const over = readSaveArgs({ bytes: big, type: "image/png" }, 0, NO_RUN, 0, tight);
  assert.equal(!over.ok && over.error.code, "too_large");
  const exact = new Uint8Array(64);
  exact.set(PNG);
  assert.equal(readSaveArgs({ bytes: exact, type: "image/png" }, 0, NO_RUN, 0, tight).ok, true);
});

test("the one-thing-per-touch rule that dictation uses is unchanged", () => {
  assert.equal(saveAllowed(1000, null, 1500), "ok");
  assert.equal(saveAllowed(1000, 1500, 1600), "tooSoon");
  assert.equal(saveAllowed(1000, null, 1000 + SAVE_WINDOW_MS + 1), "noGesture");
});

test("a page the owner has not just touched cannot save a file", () => {
  const args = { text: "hello" };
  const refused = readSaveArgs(args, 1000, NO_RUN, 1000 + SAVE_WINDOW_MS + 1);
  assert.equal(!refused.ok && refused.error.code, "not_allowed");
  assert.equal(!refused.ok && refused.error.message, SAVE_REFUSALS.noGesture);
  assert.ok(!refused.ok && refused.error.owner_message);
  assert.equal(readSaveArgs(args, null, NO_RUN, 0).ok, false);
  assert.equal(readSaveArgs(args, 2000, NO_RUN, 1000).ok, false); // a touch from the future
  assert.equal(readSaveArgs(args, 1000, NO_RUN, 1000 + SAVE_WINDOW_MS).ok, true);
});

test("the window for saving after a touch is a few seconds, not minutes", () => {
  assert.ok(SAVE_WINDOW_MS <= 5000);
  assert.equal(readSaveArgs({ text: "x" }, 1000, NO_RUN, 1000 + 30_000).ok, false);
});

function saveFlow(limits: SaveLimits = DEFAULT_SAVE_LIMITS) {
  let run: SaveRun = newRun();
  return {
    try(touchedAt: number | null, now: number) {
      const read = readSaveArgs({ text: "x" }, touchedAt, run, now, limits);
      if (read.ok && touchedAt !== null) run = recordSave(run, touchedAt, now);
      return read;
    },
    get run() {
      return run;
    },
  };
}

test("one touch saves a few files in a row, then no more until the owner touches the page again", () => {
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 3 });
  assert.equal(flow.try(50_000, 50_100).ok, true);
  assert.equal(flow.try(50_000, 50_200).ok, true);
  assert.equal(flow.try(50_000, 50_300).ok, true);
  const fourth = flow.try(50_000, 50_400);
  assert.equal(!fourth.ok && fourth.error.code, "quota");
  assert.equal(!fourth.ok && fourth.error.message, SAVE_REFUSALS.tooMany(3));
  assert.match(!fourth.ok ? (fourth.error.owner_message ?? "") : "", /more than the 3 files one click may save/);
  assert.equal(flow.run.count, 3); // a refused save is not counted
  assert.equal(flow.try(50_000, 52_000).ok, false); // still the same touch
  assert.equal(flow.try(60_000, 60_100).ok, true); // a new touch starts a new run
  assert.equal(flow.run.count, 1);
});

test("with the owner's limit at one, a second save straight after the first is refused as it always was", () => {
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 1 });
  assert.equal(flow.try(50_000, 50_001).ok, true);
  const second = flow.try(50_000, 50_002);
  assert.equal(!second.ok && second.error.code, "quota");
  assert.equal(flow.try(53_000, 53_001).ok, true);
});

test("each file of a run must follow the one before within the window, and the whole run within its limit", () => {
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 20 });
  assert.equal(flow.try(10_000, 10_000 + SAVE_WINDOW_MS).ok, true);
  // A pause longer than the window ends the run.
  const late = flow.try(10_000, 10_000 + SAVE_WINDOW_MS + SAVE_WINDOW_MS + 1);
  assert.equal(!late.ok && late.error.code, "not_allowed");
  // Steady saves a few seconds apart go on, until the run's own limit.
  const steady = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 20 });
  let at = 10_000;
  const results: boolean[] = [];
  while (at <= 10_000 + SAVE_RUN_MS + 4000) {
    results.push(steady.try(10_000, at).ok);
    at += 4000;
  }
  assert.deepEqual(results, [true, true, true, true, true, true, true, true, false]);
  assert.equal(saveRunAllowed(10_000, newRun(), 10_000 + SAVE_WINDOW_MS + 1, 5), "noGesture");
});

test("a save whose time is before the last one, or whose touch is newer than the run, starts or refuses sensibly", () => {
  const run = recordSave(newRun(), 1000, 3000);
  assert.equal(saveRunAllowed(1000, run, 2000, 5), "noGesture"); // time went backwards
  assert.equal(saveRunAllowed(9000, run, 9500, 5), "ok"); // a newer touch: a fresh run
  assert.deepEqual(recordSave(run, 9000, 9500), { touchedAt: 9000, count: 1, lastAt: 9500 });
  assert.deepEqual(recordSave(run, 1000, 3500), { touchedAt: 1000, count: 2, lastAt: 3500 });
});

test("asking again during one click's activation does not make a new touch, so one click saves only its few files", () => {
  const touch = createTouch();
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 2 });
  const saves: boolean[] = [];
  for (const at of [10_000, 10_500, 11_000, 12_000, 14_000, 14_900]) {
    observe(touch, TOUCH, true, at); // the page sends a message, the activation still alive
    saves.push(flow.try(touch.touchedAt, at).ok);
  }
  assert.deepEqual(saves, [true, true, false, false, false, false]);
  observe(touch, IDLE, true, 16_000); // the activation ended
  observe(touch, TOUCH, true, 20_000); // the owner's next click
  assert.equal(flow.try(touch.touchedAt, 20_000).ok, true);
});

test("a page that says the owner clicked every 300 ms after one real click is still one touch", () => {
  // The probe of the review: one real click, then `ui.touch` every 300 ms while the browser says active.
  const touch = createTouch();
  focusEntered(touch, TOUCH, 0);
  const touches = new Set<number | null>([touch.touchedAt]);
  for (let now = 300; now <= 4900; now += 300) {
    hint(touch, TOUCH, true, now);
    touches.add(touch.touchedAt);
  }
  assert.deepEqual([...touches], [0]);
});

test("a caller that still passes the old fifth argument, the page's word, gets no new touch either", () => {
  const touch = createTouch();
  focusEntered(touch, TOUCH, 0);
  const old = observe as unknown as (...args: unknown[]) => void;
  for (let now = 300; now <= 4900; now += 300) old(touch, TOUCH, true, now, true);
  assert.equal(touch.touchedAt, 0);
});

test("a forged word of a click saves no more files than the owner's limit from one real click", () => {
  const touch = createTouch();
  const limits = { ...DEFAULT_SAVE_LIMITS, maxParts: 5, perMinute: 100 };
  const flow = saveFlow(limits);
  focusEntered(touch, TOUCH, 0);
  let saved = 0;
  for (let now = 300; now <= 4900; now += 300) {
    hint(touch, TOUCH, true, now); // what `ui.touch` does: look now, and no more
    if (flow.try(touch.touchedAt, now).ok) saved += 1;
  }
  assert.equal(saved, 5);
  assert.equal(flow.run.count, 5);
});

test("a forged word of a click never makes dictation available again, or a touch newer", () => {
  const touch = createTouch();
  focusEntered(touch, TOUCH, 1000);
  assert.equal(dictateRefusal(touch.touchedAt, null, 1100, 0), null);
  const dictatedAt = 1100; // the one dictation this touch allows
  for (let now = 1400; now <= 5800; now += 300) {
    hint(touch, TOUCH, true, now);
    if (now - 1000 <= ACTIVATION_MS) {
      assert.equal(dictateRefusal(touch.touchedAt, dictatedAt, now, 0)?.code, "quota", `at ${now}`);
    }
  }
  // The browser's activation ends: the touch is as old as it was, and its window is over.
  observe(touch, IDLE, true, 6100);
  assert.equal(touch.touchedAt, 1000);
  assert.equal(dictateRefusal(touch.touchedAt, dictatedAt, 6100, 0)?.code, "not_allowed");
});

test("a real click after the browser's activation ended is a new touch, and it starts a new run", () => {
  const touch = createTouch();
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 2, perMinute: 100 });
  focusEntered(touch, TOUCH, 10_000);
  assert.equal(flow.try(touch.touchedAt, 10_050).ok, true);
  assert.equal(flow.try(touch.touchedAt, 10_100).ok, true);
  assert.equal(flow.try(touch.touchedAt, 10_150).ok, false);
  observe(touch, IDLE, true, 16_000); // seen to end
  observe(touch, TOUCH, true, 20_000); // the owner's next click
  assert.equal(touch.touchedAt, 20_000);
  assert.equal(flow.try(touch.touchedAt, 20_050).ok, true);
  assert.equal(flow.run.count, 1);
});

test("a click that renews a live activation is a new touch once the first one's time has passed", () => {
  const touch = createTouch();
  observe(touch, TOUCH, true, 10_000);
  observe(touch, TOUCH, true, 12_000); // a second real click two seconds later: the same touch
  observe(touch, TOUCH, true, 14_000); // and a third
  assert.equal(touch.touchedAt, 10_000);
  observe(touch, TOUCH, true, 10_000 + ACTIVATION_MS + 1); // the browser still says active: someone clicked
  assert.equal(touch.touchedAt, 10_000 + ACTIVATION_MS + 1);
});

test("three real clicks two seconds apart, each saving one file, each save is allowed", () => {
  // The cost of not believing the page: they share one touch's run, so they are allowed as long
  // as the run has files left. (A run that is used up waits for the activation to run out.)
  const touch = createTouch();
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 5, perMinute: 100 });
  const saves: boolean[] = [];
  for (const click of [10_000, 12_000, 14_000]) {
    observe(touch, TOUCH, true, click);
    saves.push(flow.try(touch.touchedAt, click + 50).ok);
  }
  assert.deepEqual(saves, [true, true, true]);
  assert.equal(flow.run.count, 3);
});

test("one click that hands over several files is still one run, whatever the page says in the meantime", () => {
  const touch = createTouch();
  const flow = saveFlow({ ...DEFAULT_SAVE_LIMITS, maxParts: 3 });
  observe(touch, TOUCH, true, 10_000);
  const saves: boolean[] = [];
  for (const at of [10_050, 10_100, 10_150, 10_200]) {
    observe(touch, TOUCH, true, at); // a message from the page, with no new word of a click
    saves.push(flow.try(touch.touchedAt, at).ok);
  }
  assert.deepEqual(saves, [true, true, true, false]);
});

test("the page's word that the owner clicked starts nothing when the browser says no one touched the tab", () => {
  const touch = createTouch();
  observe(touch, IDLE, true, 10_000);
  assert.equal(touch.touchedAt, null);
  observe(touch, undefined, true, 10_000); // a browser that cannot say
  assert.equal(touch.touchedAt, null);
  shellInput(touch, 20_000); // a click in the shell, not in the frame
  observe(touch, TOUCH, true, 20_100);
  assert.equal(touch.touchedAt, null);
  observe(touch, TOUCH, false, 20_100); // the frame does not hold the focus
  assert.equal(touch.touchedAt, null);
});

test("however many touches the owner makes, one app still saves at most the owner's limit in a minute", () => {
  const touch = createTouch();
  const ledger = newLedger();
  const limits = { ...DEFAULT_SAVE_LIMITS, maxParts: 1, perMinute: 4 };
  let run = newRun();
  const results: boolean[] = [];
  for (let click = 0; click < 8; click++) {
    const at = 10_000 + click * 6000; // eight real clicks, each after the last one's activation ended
    observe(touch, IDLE, true, at - 500);
    observe(touch, TOUCH, true, at);
    const read = readSaveArgs({ text: "x" }, touch.touchedAt, run, at, limits, savesInMinute(ledger, "water", at));
    results.push(read.ok);
    if (read.ok) {
      run = recordSave(run, touch.touchedAt ?? at, at);
      noteSaved(ledger, "water", read.save, at);
    }
  }
  assert.deepEqual(results, [true, true, true, true, false, false, false, false]);
});

test("the page's word that the owner clicked is a call the shell answers itself, and takes no argument that matters", () => {
  assert.equal(CALLS["ui.touch"]?.shell, true);
  assert.equal(CALLS["ui.touch"]?.needs, undefined);
});

test("an activation from a click in the shell, not the frame, is never the frame's touch", () => {
  const touch = createTouch();
  shellInput(touch, 10_000); // the owner clicked the sidebar
  observe(touch, TOUCH, true, 10_200); // the page asks at once, the frame holding the focus
  assert.equal(touch.touchedAt, null);
  observe(touch, TOUCH, false, 10_300); // or with the focus elsewhere
  assert.equal(touch.touchedAt, null);
  assert.equal(readSaveArgs({ text: "x" }, touch.touchedAt, NO_RUN, 10_300).ok, false);
  // The owner then clicks into the frame: the focus moves in, after the shell's click.
  focusEntered(touch, TOUCH, 11_000);
  assert.equal(touch.touchedAt, 11_000);
  // With the focus in the frame and no shell input since, a later click counts.
  observe(touch, IDLE, true, 17_000);
  observe(touch, TOUCH, true, 18_000);
  assert.equal(touch.touchedAt, 18_000);
});

test("focus moving into the frame without an activation is not a touch", () => {
  const touch = createTouch();
  focusEntered(touch, IDLE, 5000); // the page moved the focus itself
  assert.equal(touch.touchedAt, null);
  focusEntered(touch, null, 6000); // a browser that cannot say: the focus is the only sign
  assert.equal(touch.touchedAt, 6000);
  observe(touch, null, true, 7000 + ACTIVATION_MS);
  assert.equal(touch.touchedAt, 6000);
});

function fakeEnv() {
  const log: string[] = [];
  const blobs: Blob[] = [];
  const later: Array<() => void> = [];
  const env: SaveEnv = {
    makeBlob: (body) => {
      const blob = new Blob([body as unknown as Uint8Array<ArrayBuffer>], { type: "application/octet-stream" });
      blobs.push(blob);
      return blob;
    },
    createUrl: () => {
      log.push("create");
      return "blob:fake";
    },
    revokeUrl: (url) => log.push(`revoke ${url}`),
    click: (url, name) => log.push(`click ${url} ${name}`),
    later: (work) => later.push(work),
  };
  return { env, log, blobs, later };
}

test("the shell makes the object address, clicks it as a download and revokes it, and the Blob is never a page", async () => {
  const { env, log, blobs, later } = fakeEnv();
  deliver({ name: "a.html", type: "text/html", body: "<script>x</script>", bytes: 18 }, env);
  assert.deepEqual(log, ["create", "click blob:fake a.html"]);
  assert.equal(blobs[0]?.type, "application/octet-stream");
  // What was saved is the page behind its policy, never the page alone.
  const text = await blobs[0]?.text();
  assert.ok(text?.startsWith("<!doctype html><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\""));
  assert.ok(text?.endsWith("<script>x</script>"));
  later.forEach((work) => work());
  assert.deepEqual(log, ["create", "click blob:fake a.html", "revoke blob:fake"]);
});

test("text and pictures are saved exactly as the page gave them, with no policy added", async () => {
  const { env, blobs } = fakeEnv();
  deliver({ name: "a.md", type: "text/markdown", body: "<script>x</script>", bytes: 18 }, env);
  deliver({ name: "a.png", type: "image/png", body: PNG, bytes: PNG.length }, env);
  assert.equal(await blobs[0]?.text(), "<script>x</script>");
  assert.deepEqual([...new Uint8Array((await blobs[1]?.arrayBuffer()) ?? new ArrayBuffer(0))], [...PNG]);
  assert.equal(blobs[1]?.type, "application/octet-stream");
});

test("the address is revoked even when the click fails", () => {
  const { env, log, later } = fakeEnv();
  env.click = () => {
    throw new Error("blocked");
  };
  assert.throws(() => deliver({ name: "a.txt", type: "text/plain", body: "x", bytes: 1 }, env));
  later.forEach((work) => work());
  assert.deepEqual(log, ["create", "revoke blob:fake"]);
});

test("a page that sends an address or a path with the call gets a plain file and nothing else", async () => {
  const out: BridgeResponse[] = [];
  const saved: string[] = [];
  const relay = createRelay({
    appId: "articles",
    grant: { permissions: [], tier: "sandboxed" } as never,
    send: () => Promise.reject(new Error("never sent to the server")),
    post: (r) => out.push(r),
    shell: {
      "ui.save": (args) => {
        const read = readSaveArgs(args, 0, NO_RUN, 0);
        if (!read.ok) throw read.error;
        saved.push(read.save.name);
        return { name: read.save.name, bytes: read.save.bytes };
      },
    },
  });
  await relay.handle({ id: 1, call: "ui.save", args: { text: "ok", name: "../../x", url: "https://example.com" } });
  await relay.handle({ id: 2, call: "ui.save", args: { text: "ok", name: "../../x" } });
  assert.equal(out[0]?.ok, false);
  assert.equal((out[0] as { error: { code: string } }).error.code, "invalid");
  assert.equal(out[1]?.ok, true);
  assert.deepEqual(saved, ["x.txt"]);
});

// --- pictures and other bytes -----------------------------------------------------------------

test("a page can save a PNG, a JPEG or a WebP as bytes and gets the right ending", () => {
  const png = ok({ bytes: PNG, type: "image/png", name: "scene" });
  assert.deepEqual([png.name, png.type, png.bytes], ["scene.png", "image/png", PNG.length]);
  assert.ok(png.body instanceof Uint8Array);
  assert.equal(ok({ bytes: JPEG, type: "image/jpeg", name: "photo.jpeg" }).name, "photo.jpg");
  assert.equal(ok({ bytes: JPEG, type: "image/jpg", name: "photo" }).name, "photo.jpg");
  assert.equal(ok({ bytes: WEBP.buffer, type: "IMAGE/WEBP; q=1", name: "photo" }).name, "photo.webp");
  assert.equal(ok({ bytes: new DataView(PNG.buffer.slice(0) as ArrayBuffer), type: "image/png" }).name, "file.png");
  assert.equal(pictureType("image/gif"), null);
  assert.equal(pictureType("image/svg+xml"), null);
});

test("bytes that do not start like the picture they claim to be are refused, so a .png is never a page in disguise", () => {
  const page = new TextEncoder().encode("<html><script>1</script></html>");
  for (const [bytes, type] of [[page, "image/png"], [page, "image/jpeg"], [page, "image/webp"], [PNG, "image/jpeg"], [JPEG, "image/webp"], [WEBP, "image/png"], [new Uint8Array(3), "image/png"]] as const) {
    const read = readSaveArgs({ bytes, type }, T, NO_RUN, T);
    assert.equal(!read.ok && read.error.code, "invalid", type);
    assert.equal(!read.ok && read.error.message, SAVE_REFUSALS.notThatPicture);
  }
  assert.equal(looksLike("image/webp", Uint8Array.from([...Buffer.from("RIFF"), 0, 0, 0, 0, ...Buffer.from("WAVE")])), false);
  assert.equal(looksLike("image/png", PNG), true);
  assert.equal(looksLike("text/plain", PNG), false);
});

test("bytes need a picture type, a picture type needs bytes, and a save is text or bytes, never both or neither", () => {
  const cases: [unknown, string][] = [
    [{ bytes: PNG }, SAVE_REFUSALS.notPicture],
    [{ bytes: PNG, type: "text/plain" }, SAVE_REFUSALS.notPicture],
    [{ bytes: PNG, type: "text/html" }, SAVE_REFUSALS.notPicture],
    [{ bytes: PNG, type: "image/svg+xml" }, SAVE_REFUSALS.notPicture],
    [{ bytes: PNG, type: "application/x-msdownload" }, SAVE_REFUSALS.notPicture],
    [{ text: "iVBORw0KGgo", type: "image/png" }, SAVE_REFUSALS.pictureAsText],
    [{ bytes: new Uint8Array(0), type: "image/png" }, SAVE_REFUSALS.noBytes],
    [{ bytes: PNG, text: "x", type: "image/png" }, SAVE_REFUSALS.shape],
    [{ type: "image/png" }, SAVE_REFUSALS.shape],
  ];
  for (const [args, message] of cases) {
    const read = readSaveArgs(args, T, NO_RUN, T);
    assert.equal(!read.ok && read.error.message, message, JSON.stringify(args, (_k, v) => (v instanceof Uint8Array ? "bytes" : v)));
  }
});

test("bytes that are not bytes are refused: a string, a list of numbers, an object, a Blob, a shared buffer", () => {
  const shared = typeof SharedArrayBuffer === "function" ? new SharedArrayBuffer(16) : null;
  const odd: unknown[] = ["abc", [137, 80, 78, 71], { length: 4 }, 5, null, new Blob([PNG]), ...(shared ? [shared] : [])];
  for (const bytes of odd) {
    const read = readSaveArgs({ bytes, type: "image/png" }, T, NO_RUN, T);
    assert.equal(!read.ok && read.error.code, "invalid", String(bytes));
  }
});

test("the bytes saved are a copy, so the page cannot change them after the shell has looked", () => {
  const mine = Uint8Array.from(PNG);
  const saved = ok({ bytes: mine, type: "image/png" });
  mine[0] = 0x3c; // the page turns it into markup afterwards
  assert.equal((saved.body as Uint8Array)[0], 0x89);
  const view = new Uint8Array(Uint8Array.from([0, 0, ...PNG]).buffer, 2);
  assert.deepEqual([...(ok({ bytes: view, type: "image/png" }).body as Uint8Array)], [...PNG]);
});

test("bytes survive the trip over a message port as the shell receives them", async () => {
  const channel = new MessageChannel();
  const got = new Promise<unknown>((resolve) => channel.port1.on("message", resolve));
  channel.port2.postMessage({ id: 1, call: "ui.save", args: { bytes: PNG.buffer.slice(0), type: "image/png", name: "x" } });
  const message = (await got) as { args: unknown };
  const read = readSaveArgs(message.args, T, NO_RUN, T);
  assert.equal(read.ok, true);
  channel.port1.close();
  channel.port2.close();
});

test("a relay hands a save with bytes to the shell whole, where a call that is plain JSON would be refused for its size", async () => {
  const out: BridgeResponse[] = [];
  const seen: unknown[] = [];
  const relay = createRelay({
    appId: "imagery",
    grant: { permissions: [], tier: "sandboxed" } as never,
    send: () => Promise.reject(new Error("never sent to the server")),
    post: (r) => out.push(r),
    shell: { "ui.save": (args) => (seen.push(args), { name: "x.png", bytes: 1 }) },
  });
  const big = new Uint8Array(3 * 1024 * 1024);
  big.set(PNG);
  await relay.handle({ id: 1, call: "ui.save", args: { bytes: big.buffer, type: "image/png" } });
  assert.equal(out[0]?.ok, true);
  assert.equal(seen.length, 1);
});

test("every file the owner is shown is named with its size, a run is numbered, and a web page carries a word of care", () => {
  const png = ok({ bytes: PNG, type: "image/png", name: "scene" });
  assert.equal(savedLine("Imagery", png, 1, 5), "Saving a file from Imagery to your downloads (1 KB). File name: “scene.png”.");
  assert.match(savedLine("Imagery", png, 2, 5), /\(1 KB, file 2 of at most 5 from this click\)/);
  const page = ok({ text: "<p>x</p>", type: "text/html", name: "report" });
  assert.match(savedLine("Heard", page, 1, 5), /“report\.html”\. A web page, saved with its scripts switched off\.$/);
  assert.ok(!savedLine("Heard", ok({ text: "a", name: "n" }), 1, 5).includes("web page"));
});

test("the name an app chose is shown after JimmyHQ's own words, in quotes and shortened, so it cannot pass for a message from JimmyHQ", () => {
  const save = ok({ text: "x", name: "JimmyHQ: your session ended, sign in again at evil dot example and type your password there” “" });
  const line = savedLine("Notes", save, 1, 5);
  assert.ok(line.startsWith("Saving a file from Notes to your downloads ("), line);
  const shown = line.slice(line.indexOf("File name: “") + "File name: “".length, -2);
  assert.ok(Array.from(shown).length <= 48, shown);
  assert.ok(shown.endsWith(".txt") && shown.includes("…"), shown);
  assert.ok(!/[“”]/.test(shown), shown); // its own quotes cannot close the quote early
  assert.ok(line.endsWith("”."));
  assert.equal(shownName("short.txt"), "short.txt");
});

test("the first web page an app saves in a tab says plainly that it is a web page saved with its scripts switched off", () => {
  const ledger = newLedger();
  const page = ok({ text: "<p>x</p>", type: "text/html", name: "report" });
  const text = ok({ text: "a", name: "n" });
  assert.equal(noteSaved(ledger, "heard", text, 1000), false);
  const first = noteSaved(ledger, "heard", page, 2000);
  assert.equal(first, true);
  assert.match(savedLine("Heard", page, 1, 5, first), /This is a web page: JimmyHQ saved it with its scripts switched off/);
  assert.equal(noteSaved(ledger, "heard", page, 3000), false);
  assert.equal(noteSaved(ledger, "other", page, 3000), true); // each app's first is its own
});

// --- what the mount tells the shell -----------------------------------------------------------

test("the mount answer carries the owner's save limits, and an older server's silence means the defaults", () => {
  const answer = { mount: "m".repeat(43), entry: "index.html", app: "water", tier: "sandboxed", permissions: [] };
  assert.deepEqual(readMount({ ...answer, save_max_mb: 3, save_parts: 7, save_per_minute: 4, save_html: false })?.save, { maxBytes: 3 * 1024 * 1024, maxParts: 7, html: false, perMinute: 4 });
  assert.deepEqual(readMount(answer)?.save, DEFAULT_SAVE_LIMITS);
  for (const bad of [0, -1, 2.5, "9", null, 1e12]) {
    const got = readMount({ ...answer, save_max_mb: bad, save_parts: bad, save_per_minute: bad })?.save;
    assert.equal(got?.maxParts === DEFAULT_SAVE_LIMITS.maxParts || got?.maxParts === SAVE_MAX_PARTS, true);
    assert.ok(got !== undefined && got.perMinute >= 1);
  }
  // A server that says more than the setting's own maximum is held to it.
  assert.equal(readMount({ ...answer, save_parts: 20 })?.save.maxParts, SAVE_MAX_PARTS);
  assert.equal(readMount({ ...answer, save_html: "no" })?.save.html, true);
});

test("a page that gets a fresh click every few seconds still saves no more files a minute than the owner allows", () => {
  // The reviewer's spray: a page that keeps the owner clicking (a fresh activation every
  // five seconds) and saves as fast as each click allows.
  const ledger = newLedger();
  const limits: SaveLimits = { ...DEFAULT_SAVE_LIMITS, perMinute: 12 };
  let run = newRun();
  let saved = 0;
  let touchedAt = 0;
  for (let now = 0; now < SAVE_MINUTE_MS; now += 100) {
    if (now - touchedAt >= 5000) touchedAt = now;
    const read = readSaveArgs({ text: "x" }, touchedAt, run, now, limits, savesInMinute(ledger, "spray", now));
    if (read.ok) {
      run = recordSave(run, touchedAt, now);
      noteSaved(ledger, "spray", read.save, now);
      saved++;
    } else if (read.error.code === "quota" && read.error.message === SAVE_REFUSALS.tooFast(12)) {
      assert.equal(read.error.owner_message, SAVE_OWNER_REFUSALS.tooFast(12));
    }
  }
  assert.equal(saved, 12);
  // A minute after the first, the count has moved on and a click saves again.
  assert.equal(savesInMinute(ledger, "spray", SAVE_MINUTE_MS + 1) < 12, true);
  // Another app has its own count.
  assert.equal(savesInMinute(ledger, "other", 1000), 0);
});
