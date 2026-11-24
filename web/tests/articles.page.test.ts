// The Articles page as a whole: when it draws again, how it shows times, and that no file
// of it writes HTML from a string, reaches the network or uses the browser's dialogs.

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { afterChange } from "../../apps/articles/web/lib/redraw.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { formatWhen, relative, zoneFor } from "../../apps/articles/web/lib/time.js";
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
import { shownChoices, zoneOptions } from "../../apps/articles/web/lib/zones.js";

test("a change redraws a list at once but never wipes a form the owner is typing in", () => {
  assert.equal(afterChange({ view: "today" }), "all");
  assert.equal(afterChange({ view: "articles", focused: true }), "nav"); // the search box keeps its place
  assert.equal(afterChange({ view: "sites", typed: true }), "nav");
  assert.equal(afterChange({ view: "sites", typed: false }), "all");
  assert.equal(afterChange({ view: "new", typed: true }), "all"); // New keeps what was typed
  assert.equal(afterChange({ view: "ideas", typed: true, focused: true }), "nav");
});

test("times are shown in the site's zone, else the owner's", () => {
  const ms = Date.UTC(2027, 0, 15, 8, 0);
  assert.equal(formatWhen(ms, "UTC", ms), "Fri 15 Jan, 08:00");
  assert.equal(formatWhen(ms, "Asia/Tokyo", ms), "Fri 15 Jan, 17:00");
  assert.equal(formatWhen(ms, "UTC", Date.UTC(2028, 0, 1)), "Fri 15 Jan 2027, 08:00");
  assert.equal(zoneFor({ timezone: "Europe/Lisbon" }, "UTC"), "Europe/Lisbon");
  assert.equal(zoneFor({ timezone: "" }, "Asia/Tokyo"), "Asia/Tokyo");
  assert.equal(zoneFor({ timezone: "Mars/Olympus" }, "UTC"), "UTC");
  assert.equal(relative(ms - 3 * 86_400_000, ms), "3 days ago");
  assert.equal(relative(ms, ms), "just now");
});

test("the site's time zone picker offers Use mine first and finds a zone by city", () => {
  const options = zoneOptions(["Europe/Lisbon", "Asia/Tokyo", "UTC"], new Date(Date.UTC(2027, 0, 15)));
  assert.equal(shownChoices(options, "", "UTC")[0].zone, "");
  assert.deepEqual(shownChoices(options, "tokyo", "UTC").map((o: { zone: string }) => o.zone), ["Asia/Tokyo"]);
});

test("no file of the page writes HTML from a string, reaches the network, or uses the browser's dialogs", () => {
  const root = new URL("../../apps/articles/web/", import.meta.url);
  const files = readdirSync(root, { recursive: true }).map(String).filter((f) => f.endsWith(".js") || f.endsWith(".html"));
  assert.ok(files.length >= 15);
  for (const file of files) {
    const text = readFileSync(new URL(file, root), "utf8");
    for (const banned of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "alert(", "confirm(", "prompt(",
      "fetch(", "XMLHttpRequest", "WebSocket", "http://", "eval(", "new Function", "location."]) {
      assert.ok(!text.includes(banned), `${file} uses ${banned}`);
    }
  }
});
