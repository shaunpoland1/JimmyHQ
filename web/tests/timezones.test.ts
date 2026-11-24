import assert from "node:assert/strict";
import { test } from "node:test";
import { deviceTimeZone, filterTimeZones, formatOffset, isKnownZone, offsetMinutes, timeZoneNames, timeZoneOptions, zoneName } from "../src/lib/timeZones.ts";

const winter = new Date(Date.UTC(2026, 0, 15, 12));
const summer = new Date(Date.UTC(2026, 6, 15, 12));

test("an offset reads as hours and minutes from UTC", () => {
  assert.equal(formatOffset(0), "UTC");
  assert.equal(formatOffset(120), "UTC+02:00");
  assert.equal(formatOffset(-300), "UTC-05:00");
  assert.equal(formatOffset(330), "UTC+05:30");
  assert.equal(formatOffset(-210), "UTC-03:30");
});

test("the offset of a zone follows its summer time", () => {
  assert.equal(offsetMinutes("Europe/Paris", winter), 60);
  assert.equal(offsetMinutes("Europe/Paris", summer), 120);
  assert.equal(offsetMinutes("Asia/Kolkata", winter), 330);
  assert.equal(offsetMinutes("America/New_York", winter), -300);
  assert.equal(offsetMinutes("UTC", winter), 0);
});

test("a zone nobody knows has no offset and is not offered as this device's", () => {
  assert.equal(offsetMinutes("Mars/Olympus", winter), null);
  assert.ok(!isKnownZone("Mars/Olympus"));
  assert.ok(!isKnownZone(""));
  assert.ok(isKnownZone("Europe/Paris"));
  const device = deviceTimeZone();
  assert.ok(device === null || isKnownZone(device));
});

test("the list is sorted by name, holds UTC, and keeps a stored value the browser does not list", () => {
  const names = timeZoneNames(["Old/Stored", null, ""], ["Europe/Paris", "America/New_York", "Asia/Tokyo"]);
  assert.deepEqual(names, ["America/New_York", "Asia/Tokyo", "Europe/Paris", "Old/Stored", "UTC"]);
  assert.ok(timeZoneNames().includes("UTC"));
});

test("names are shown with spaces and stored as they are", () => {
  assert.equal(zoneName("America/New_York"), "America/New York");
  const [option] = timeZoneOptions(["America/New_York"], winter);
  assert.deepEqual(option, { zone: "America/New_York", name: "America/New York", offset: "UTC-05:00" });
  assert.equal(timeZoneOptions(["Mars/Olympus"], winter)[0]?.offset, "");
});

const options = timeZoneOptions(["America/New_York", "America/North_Dakota/New_Salem", "Asia/Kolkata", "Europe/Paris", "Pacific/Auckland", "UTC"], winter);
const found = (query: string) => filterTimeZones(options, query).map((option) => option.zone);

test("typing finds a zone by city or region, however it is spaced or cased", () => {
  assert.deepEqual(found("  "), options.map((option) => option.zone));
  assert.deepEqual(found("paris"), ["Europe/Paris"]);
  assert.deepEqual(found("New York"), ["America/New_York"]);
  assert.deepEqual(found("new_york"), ["America/New_York"]);
  assert.deepEqual(found("york america"), ["America/New_York"]);
  assert.deepEqual(found("america/new"), ["America/New_York", "America/North_Dakota/New_Salem"]);
  assert.deepEqual(found("zzz"), []);
});

test("a name that starts with what was typed comes before one that only contains it", () => {
  assert.deepEqual(found("a").slice(0, 3), ["America/New_York", "America/North_Dakota/New_Salem", "Asia/Kolkata"]);
  assert.deepEqual(found("auck"), ["Pacific/Auckland"]);
});

test("typing an offset finds the zones on it", () => {
  assert.deepEqual(found("+5:30"), ["Asia/Kolkata"]);
  assert.deepEqual(found("utc-5"), ["America/New_York"]);
  assert.deepEqual(found("GMT+1"), ["Europe/Paris"]);
  assert.deepEqual(found("+13"), ["Pacific/Auckland"]);
  assert.deepEqual(found("+05"), ["Asia/Kolkata"]);
});
