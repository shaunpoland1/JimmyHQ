import assert from "node:assert/strict";
import { test } from "node:test";
import { displayTotal, displayValue, formatDate, formatMoney, formatStamp, minorDigits, moneyInput, parseMoney, totalWord } from "../src/modules/apps/described/lib/values.ts";

const ctx = { labels: { author: { "3": "Avery Example" } }, locale: "en-US" };

test("money is whole minor units shown with the currency's own number of decimals", () => {
  assert.equal(minorDigits("USD", "en-US"), 2);
  assert.equal(minorDigits("JPY", "en-US"), 0);
  assert.equal(minorDigits("BHD", "en-US"), 3);
  assert.equal(formatMoney(1250, "USD", "en-US"), "$12.50");
  assert.equal(formatMoney(1250, "JPY", "en-US"), "¥1,250");
  assert.equal(moneyInput(1205, "USD", "en-US"), "12.05");
  assert.equal(moneyInput(-5, "USD", "en-US"), "-0.05");
});

test("a typed amount becomes minor units exactly, or is refused", () => {
  assert.equal(parseMoney("12.50", "USD", "en-US"), 1250);
  assert.equal(parseMoney("12,5", "USD", "en-US"), 1250);
  assert.equal(parseMoney("12", "USD", "en-US"), 1200);
  assert.equal(parseMoney(" 0.07 ", "USD", "en-US"), 7);
  assert.equal(parseMoney("-3.20", "USD", "en-US"), -320);
  assert.equal(parseMoney("1250", "JPY", "en-US"), 1250);
  assert.equal(parseMoney("0.1", "USD", "en-US"), 10);
  for (const bad of ["", "abc", "12.345", "1e3", "$5", "1.2.3", "99999999999999999999", "12.5.0"]) assert.equal(parseMoney(bad, "USD", "en-US"), null, bad);
  assert.equal(parseMoney("12.5", "JPY", "en-US"), null);
});

test("each kind of value shows as plain text", () => {
  assert.equal(displayValue({ type: "int" }, "n", 1234, ctx), "1,234");
  assert.equal(displayValue({ type: "number" }, "n", 2.5, ctx), "2.5");
  assert.equal(displayValue({ type: "bool" }, "b", true, ctx), "Yes");
  assert.equal(displayValue({ type: "bool" }, "b", false, ctx), "No");
  assert.equal(displayValue({ type: "date" }, "d", "2030-11-18", ctx), "Nov 18, 2030");
  assert.equal(displayValue({ type: "choice", options: ["a"] }, "c", "a", ctx), "a");
  assert.equal(displayValue({ type: "money", currency: "USD" }, "m", 99, ctx), "$0.99");
  assert.equal(displayValue({ type: "computed" }, "c", 0.333333333, ctx), "0.333333");
  assert.equal(displayValue({ type: "text" }, "t", null, ctx), "");
  assert.equal(displayValue({ type: "text" }, "t", undefined, ctx), "");
});

test("a link shows the label the server gave, and a missing record says so", () => {
  assert.equal(displayValue({ type: "link", table: "author" }, "author", 3, ctx), "Avery Example");
  assert.equal(displayValue({ type: "link", table: "author" }, "author", 9, ctx), "Missing record");
});

test("a value of the wrong kind for its field shows as nothing or as text, never as a failure", () => {
  assert.equal(displayValue({ type: "int" }, "n", "5" as unknown as number, ctx), "");
  assert.equal(displayValue({ type: "date" }, "d", 5 as unknown as string, ctx), "");
  assert.equal(displayValue({ type: "date" }, "d", "<b>x</b>", ctx), "<b>x</b>");
  assert.equal(displayValue({ type: "text" }, "t", 7, ctx), "7");
});

test("long and invisible text in a value is cleaned and cut", () => {
  const shown = displayValue({ type: "text" }, "t", `a\u0000${"b".repeat(500)}\nc`, ctx);
  assert.ok(Array.from(shown).length <= 200 && !shown.includes("\u0000"));
});

test("a date is shown as the day it holds, in whatever time zone the computer is in", () => {
  assert.equal(formatDate("2030-01-01", "en-US"), "Jan 1, 2030");
  assert.equal(formatDate("2030-12-31", "en-US"), "Dec 31, 2030");
  assert.equal(formatDate("not a date", "en-US"), "not a date");
});

test("a stored time is shown in the owner's time zone, and an unknown zone falls back to UTC", () => {
  const ts = Date.UTC(2030, 0, 1, 23, 30);
  assert.match(formatStamp(ts, "Asia/Tokyo", "en-US"), /Jan 2, 2030/);
  assert.match(formatStamp(ts, "America/Los_Angeles", "en-US"), /Jan 1, 2030/);
  assert.match(formatStamp(ts, "Not/AZone", "en-US"), /Jan 1, 2030/);
});

test("totals are worded and formatted by the kind of field they total", () => {
  assert.equal(displayTotal("sum", { type: "money", currency: "USD" }, 2500, "en-US"), "$25.00");
  assert.equal(displayTotal("avg", { type: "int" }, 4.5, "en-US"), "4.5");
  assert.equal(displayTotal("count", { type: "text" }, 3, "en-US"), "3");
  assert.equal(displayTotal("min", { type: "date" }, "2030-01-02", "en-US"), "Jan 2, 2030");
  assert.equal(displayTotal("sum", { type: "int" }, null, "en-US"), "");
  assert.equal(totalWord("sum"), "Total");
  assert.equal(totalWord("min", { type: "date" }), "Earliest");
  assert.equal(totalWord("avg"), "Average");
});
