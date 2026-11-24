// The Imagery page's pure words and rules: what it says about limits, runs and saved details.

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { anyActive, cleanSearch, countChoices, detailsName, detailsText, galleryLine, likeThis, makeProblem, runLine, sizeText, todayLine, whenText } from "../../apps/imagery/web/lib/format.js";

const row = { id: 7, kind: "made", prompt: "A red kite over a hill", provider: "gemini", model: "m-1", aspect: "3:2", style: "Photo", created: 1_800_000_000_000, width: 1536, height: 1024 };

test("the page shows a refusal as JimmyHQ gives it and strips nothing from the front of it", () => {
  const texts = files("../apps/imagery/web").filter((p) => p.endsWith(".js")).map((p) => readFileSync(p, "utf8")).join("\n");
  assert.doesNotMatch(texts, /could not do that/);
  assert.doesNotMatch(texts, /plainSentence|LEAD_IN/);
  assert.doesNotMatch(texts, /error\[key\]\s*=/);
});

test("a stored time is shown in the owner's own time zone", () => {
  assert.match(whenText(1_800_000_000_000, "UTC"), /15 Jan 2027/);
  assert.notEqual(whenText(1_800_000_000_000, "Pacific/Auckland"), whenText(1_800_000_000_000, "UTC"));
  assert.equal(whenText(Number.NaN, "UTC"), "");
  assert.match(whenText(1_800_000_000_000, "No/Such_Zone"), /2027/);
});

test("sizes are said in KB and MB", () => {
  assert.equal(sizeText(100), "1 KB");
  assert.equal(sizeText(840 * 1024), "840 KB");
  assert.equal(sizeText(3.4 * 1024 * 1024), "3.4 MB");
  assert.equal(sizeText(400 * 1024 * 1024), "400 MB");
});

test("the day's count says how many are used and left, and that practice uses none", () => {
  assert.equal(todayLine({ used: 18, limit: 20 }, true), "Today: 18 of 20 pictures asked of outside services. 2 left.");
  assert.equal(todayLine({ used: 25, limit: 20 }, true), "Today: 25 of 20 pictures asked of outside services. 0 left.");
  assert.match(todayLine({ used: 0, limit: 20 }, false), /uses none of today's limit/);
});

test("the gallery line says how full the gallery is", () => {
  assert.equal(galleryLine({ pictures: 1, bytes: 2048, max_bytes: 400 * 1024 * 1024, max_pictures: 300 }), "1 picture, 2 KB of 400 MB. Most to keep: 300.");
});

test("a run is said as waiting, being made, or how it ended", () => {
  assert.equal(runLine({ state: "queued", want: 2, made: 0 }), "Waiting to make 2 pictures.");
  assert.equal(runLine({ state: "working", want: 1, made: 0 }), "Making 1 picture: 0 done.");
  assert.equal(runLine({ state: "done", want: 2, made: 2, said: "Made 2 pictures." }), "Made 2 pictures.");
  assert.equal(runLine({ state: "failed", want: 2, made: 1, said: "Made 1 of 2. Over quota." }), "Made 1 of 2. Over quota.");
});

test("the page keeps asking how it is going only while something is waiting or being made", () => {
  assert.equal(anyActive([{ state: "done" }, { state: "working" }]), true);
  assert.equal(anyActive([{ state: "queued" }]), true);
  assert.equal(anyActive([{ state: "done" }, { state: "failed" }, { state: "stopped" }]), false);
  assert.equal(anyActive(undefined), false);
});

test("more like this starts the form from the picture, keeping a style and shape that are still on offer", () => {
  const data = { presets: [{ name: "Photo", ending: "x" }], provider: { aspects: ["1:1", "3:2"] } };
  assert.deepEqual(likeThis(row, data), { prompt: "A red kite over a hill", style: "Photo", aspect: "3:2", count: 1, reference: null });
  const changed = { presets: [], provider: { aspects: ["1:1"] } };
  assert.deepEqual(likeThis(row, changed), { prompt: "A red kite over a hill", style: "", aspect: "1:1", count: 1, reference: null });
});

test("the details file says what the picture was made from and with", () => {
  const text = detailsText(row, "UTC");
  assert.match(text, /^# Picture 7\n\nA red kite over a hill\n/);
  assert.match(text, /- Image service: gemini\n- Model: m-1\n- Shape: 3:2\n- Style: Photo\n- Size: 1536 by 1024 pixels\n$/);
  assert.match(detailsText({ ...row, kind: "given" }, "UTC"), /a picture you gave Imagery/);
  assert.equal(detailsName(row), "picture-7-A red kite over a hill");
});

test("the search is tidied and cut to eight words", () => {
  assert.equal(cleanSearch("  red   kite "), "red kite");
  assert.equal(cleanSearch("a b c d e f g h i j"), "a b c d e f g h");
});

test("how many is offered from one to the most the owner allows, never above four", () => {
  assert.deepEqual(countChoices(2), [1, 2]);
  assert.deepEqual(countChoices(9), [1, 2, 3, 4]);
  assert.deepEqual(countChoices(0), [1]);
});

test("make says why it cannot be pressed: a service problem first, then what is missing", () => {
  const ok = { provider: { problem: null } };
  assert.equal(makeProblem({ prompt: "a kite", aspect: "1:1" }, ok), "");
  assert.equal(makeProblem({ prompt: "  ", aspect: "1:1" }, ok), "Say what the picture should show.");
  assert.equal(makeProblem({ prompt: "a kite", aspect: "" }, ok), "Choose a shape.");
  assert.equal(makeProblem({ prompt: "", aspect: "" }, { provider: { problem: "No key." } }), "No key.");
});

test("starring a picture asks for the gallery's counts again, so Save all starred appears at once", () => {
  const gallery = readFileSync("../apps/imagery/web/views/gallery.js", "utf8");
  const star = gallery.slice(gallery.indexOf('"picture.star"'), gallery.indexOf('"\u2605 Starred"'));
  assert.match(star, /call\("status"/);
  assert.match(star, /gallery:/);
});

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

test("no part of the Imagery page uses the browser's own dialogs or builds markup from text", () => {
  const pages = files("../apps/imagery/web").filter((p) => p.endsWith(".js"));
  assert.ok(pages.length >= 6);
  for (const path of pages) {
    const text = readFileSync(path, "utf8");
    assert.doesNotMatch(text, /\b(alert|confirm|prompt)\s*\(/, path);
    assert.doesNotMatch(text, /innerHTML|insertAdjacentHTML|document\.write|eval\(/, path);
  }
});
