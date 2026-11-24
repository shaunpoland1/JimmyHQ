import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { FieldSpec, Value } from "../src/modules/apps/described/api.ts";
import { Cell } from "../src/modules/apps/described/Cell.ts";

const EVIL = `<img src=x onerror="alert(1)"><script>alert(2)</script>`;

function html(spec: FieldSpec, value: Value, labels: Record<string, Record<string, string>> = {}): string {
  return renderToStaticMarkup(createElement(Cell, { field: "f", spec, value, labels, locale: "en-US" }));
}

test("markup smuggled in a text value is shown as text and is not part of the page", () => {
  const out = html({ type: "text" }, EVIL);
  assert.ok(!out.includes("<img") && !out.includes("<script"));
  assert.ok(out.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"));
});

test("markup smuggled in a choice, a link's label or a bad date is shown as text too", () => {
  for (const out of [
    html({ type: "choice", options: [EVIL] }, EVIL),
    html({ type: "link", table: "t" }, 3, { f: { "3": EVIL } }),
    html({ type: "date" }, EVIL),
  ]) {
    assert.ok(!out.includes("<img") && !out.includes("<script"), out);
    assert.ok(out.includes("&lt;"), out);
  }
});

test("an address in a value is only text: no value ever becomes a link, an image or a frame", () => {
  for (const [spec, value] of [
    [{ type: "text" }, "javascript:alert(1)"],
    [{ type: "text" }, "https://example.com/a.png"],
    [{ type: "choice", options: ["x"] }, "data:text/html,<b>x</b>"],
  ] as [FieldSpec, Value][]) {
    const out = html(spec, value);
    assert.ok(!/<(a|img|iframe|script|object|embed|form|link|style)[\s>]/i.test(out), out);
    assert.ok(!/href=|src=/i.test(out), out);
  }
});

test("a link field shows the label of the record it points at, or says the record is missing", () => {
  assert.ok(html({ type: "link", table: "t" }, 3, { f: { "3": "Avery Example" } }).includes("Avery Example"));
  assert.ok(html({ type: "link", table: "t" }, 9, { f: {} }).includes("Missing record"));
});

test("a very long value is cut before it reaches the page", () => {
  const out = html({ type: "text" }, "x".repeat(5000));
  assert.ok(out.length < 1000);
});

test("an empty value shows a dash and says it is empty to a screen reader", () => {
  const out = html({ type: "int" }, null);
  assert.ok(out.includes("–") && out.includes("empty"));
});
