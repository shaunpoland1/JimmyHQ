// Pages of a list, joined into one.

import type { DescribedRecord, GroupSummary, LinkLabels, ListResult, Value } from "../api.ts";

export type Records = {
  records: DescribedRecord[];
  labels: LinkLabels;
  total: number;
  totals?: Record<string, Value>;
  groups?: GroupSummary[];
  hasMore: boolean;
};

/** Joins the pages loaded so far. The counts and totals are those of the latest page. */
export function joinPages(pages: ListResult[]): Records {
  const last = pages[pages.length - 1];
  const labels: LinkLabels = {};
  for (const page of pages) {
    for (const [field, names] of Object.entries(page.labels ?? {})) labels[field] = { ...labels[field], ...names };
  }
  return {
    records: pages.flatMap((p) => p.records),
    labels,
    total: last?.total ?? 0,
    totals: last?.totals,
    groups: last?.groups,
    hasMore: Boolean(last?.next),
  };
}
