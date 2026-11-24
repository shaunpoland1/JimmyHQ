// How one value of an app is put on the screen. This is the only place an app's text becomes
// part of the page, so it is kept small and written without JSX to be read in one glance:
// every value goes in as a React text child (which the browser never parses as markup), a
// `title` is an attribute string, and nothing here makes a link, an image or an address.

import { createElement } from "react";
import type { ReactElement } from "react";
import type { FieldSpec, LinkLabels, Value } from "./api.ts";
import { MISSING_RECORD, displayValue } from "./lib/values.ts";

export type CellProps = {
  field: string;
  spec: FieldSpec;
  value: Value | undefined;
  labels: LinkLabels;
  locale?: string;
  /** Let long text wrap instead of being cut to one line. */
  wrap?: boolean;
};

const CHIP = "inline-flex max-w-full items-center truncate rounded-md border border-border bg-surface-2 px-1.5 py-px text-xs text-text-muted";

export function Cell({ field, spec, value, labels, locale, wrap = false }: CellProps): ReactElement {
  const text = displayValue(spec, field, value, { labels, locale });
  if (text === "") return createElement("span", { className: "text-text-faint" }, createElement("span", { "aria-hidden": true }, "–"), createElement("span", { className: "sr-only" }, "empty"));
  if (spec.type === "choice" || spec.type === "bool") return createElement("span", { className: CHIP, title: text }, text);
  if (spec.type === "link") {
    return text === MISSING_RECORD
      ? createElement("span", { className: "text-xs italic text-text-faint", title: "The record this pointed at is no longer there." }, text)
      : createElement("span", { className: CHIP, title: text }, text);
  }
  const numeric = spec.type === "int" || spec.type === "number" || spec.type === "money" || spec.type === "computed";
  const className = `${wrap ? "break-words" : "block truncate"}${numeric ? " tabular-nums" : ""}`;
  return createElement("span", { className, title: wrap ? undefined : text }, text);
}
