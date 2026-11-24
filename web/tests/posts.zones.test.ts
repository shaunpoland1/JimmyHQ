// The Posts page: the time zone picker on a page, chosen from a list, never typed.

import assert from "node:assert/strict";
import { test } from "node:test";
import {
  FALLBACK_ZONES, browserZones, filterZones, formatOffset, offsetMinutes, shownChoices, startIndex, stepIndex, useMineLabel, zoneName, zoneNames, zoneOptions,
// @ts-expect-error The page is plain JavaScript, served to the app's frame as it is.
} from "../../apps/posts/web/lib/zones.js";

type Option = { zone: string; name: string; offset: string };
const JAN = new Date(Date.UTC(2027, 0, 15, 12));

test("the list is the zones the browser knows, with UTC and the stored zone always in it", () => {
  const names = zoneNames(["Asia/Kathmandu", ""], ["Europe/Paris", "America/New_York"]);
  assert.deepEqual(names, ["America/New_York", "Asia/Kathmandu", "Europe/Paris", "UTC"]);
});

test("a browser that will not list its zones gets the fallback list instead", () => {
  assert.equal(browserZones({}), null);
  assert.equal(browserZones({ supportedValuesOf: () => { throw new Error("no"); } }), null);
  assert.deepEqual(browserZones({ supportedValuesOf: () => ["Europe/Paris"] }), ["Europe/Paris"]);
  const names = zoneNames([], null);
  assert.ok(names.length >= 40 && names.includes("Europe/London") && names.includes("Asia/Tokyo"));
  for (const zone of FALLBACK_ZONES) assert.notEqual(offsetMinutes(zone, JAN), null, zone);
});

test("each zone shows its name with spaces and its offset from UTC at that moment", () => {
  assert.equal(zoneName("America/New_York"), "America/New York");
  assert.equal(formatOffset(0), "UTC");
  assert.equal(formatOffset(330), "UTC+05:30");
  assert.equal(formatOffset(-300), "UTC-05:00");
  assert.deepEqual(zoneOptions(["Asia/Kolkata", "Not/AZone"], JAN), [
    { zone: "Asia/Kolkata", name: "Asia/Kolkata", offset: "UTC+05:30" }, { zone: "Not/AZone", name: "Not/AZone", offset: "" }]);
});

test("typing a city, words in any order, or an offset finds the zone", () => {
  const options = zoneOptions(["America/New_York", "Europe/Paris", "Asia/Kolkata", "Asia/Kathmandu"], JAN);
  const zones = (query: string) => filterZones(options, query).map((o: Option) => o.zone);
  assert.deepEqual(zones("new york"), ["America/New_York"]);
  assert.deepEqual(zones("york america"), ["America/New_York"]);
  assert.deepEqual(zones("+5:30"), ["Asia/Kolkata"]);
  assert.deepEqual(zones("utc-5"), ["America/New_York"]);
  assert.deepEqual(zones("+1"), ["Europe/Paris"]);
  assert.deepEqual(zones("nowhere"), []);
});

test("Use mine, naming the owner's zone, is the first choice and stores nothing", () => {
  const options = zoneOptions(["Europe/Paris", "UTC"], JAN);
  const shown = shownChoices(options, "", "America/New_York");
  assert.deepEqual(shown[0], { zone: "", name: "Use mine (America/New York)", offset: "" });
  assert.equal(shown.length, 3);
  assert.equal(useMineLabel(""), "Use mine");
  assert.ok(shownChoices(options, "paris", "UTC").every((o: Option) => o.zone !== ""), "a search shows only matches");
});

test("the list opens on the stored choice, and the arrow keys wrap round", () => {
  const shown = shownChoices(zoneOptions(["Europe/Paris", "UTC"], JAN), "", "UTC");
  assert.equal(startIndex(shown, ""), 0);
  assert.equal(startIndex(shown, "UTC"), 2);
  assert.equal(startIndex(shown, "Gone/Zone"), 0);
  assert.equal(stepIndex(2, 3, 1), 0);
  assert.equal(stepIndex(0, 3, -1), 2);
  assert.equal(stepIndex(0, 0, 1), 0);
});
