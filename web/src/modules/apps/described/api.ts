// The described app's client. A described app is DATA: its tables and views come from the
// app's manifest, its records from the server's `records.*` calls. Nothing here, or in the
// renderer, treats any of it as markup.

import { ApiError, request } from "../../../api.ts";
import { bridgeBody, bridgePath, readReply } from "../lib/bridge.ts";

export type FieldType = "text" | "int" | "number" | "bool" | "date" | "choice" | "money" | "link" | "computed";

export type FieldSpec = {
  type: FieldType;
  label?: string;
  required?: boolean;
  was?: string[];
  min?: number;
  max?: number;
  options?: string[];
  currency?: string;
  table?: string;
  formula?: string;
};

export type TableSpec = { label?: string; was?: string[]; fields: Record<string, FieldSpec> };

export type FilterRule = { field: string; op: string; value?: string | number | boolean };

export type TotalKind = "sum" | "avg" | "min" | "max" | "count";

export type ViewSpec = {
  id: string;
  label: string;
  table: string;
  fields?: string[];
  filter?: FilterRule[];
  sort?: string[];
  group?: string;
  totals?: Record<string, TotalKind>;
};

/** What a described app declares: the manifest's `tables` and `views`. */
export type Description = { tables: Record<string, TableSpec>; views: ViewSpec[] };

/** One stored value. A link is a record id; money is whole minor units; empty is null. */
export type Value = string | number | boolean | null;

export type DescribedRecord = {
  /** A whole number the server gives, counted across all the app's tables. */
  id: number;
  /** What to call the record, worked out by the server. */
  label: string;
  values: Record<string, Value>;
  /** Unix milliseconds, UTC. */
  created: number;
  updated: number;
};

/** For each link field, the labels of the records the rows point at; a missing one has no entry. */
export type LinkLabels = Record<string, Record<string, string>>;

export type GroupSummary = { value: Value; count: number; totals?: Record<string, Value> };

export type ListResult = {
  records: DescribedRecord[];
  total: number;
  labels: LinkLabels;
  /** The marker for the next page, or null at the end. */
  next: string | null;
  totals?: Record<string, Value>;
  groups?: GroupSummary[];
};

export type OneResult = { record: DescribedRecord; labels: LinkLabels };

export type ListArgs = {
  table: string;
  view?: string;
  filter?: FilterRule[];
  sort?: string[];
  search?: string;
  limit?: number;
  after?: string;
};

/** The one place that knows how a call reaches the server (`POST /api/apps/<id>/bridge/<call>`). */
export type Transport = (appId: string, call: string, args: Record<string, unknown>) => Promise<unknown>;

const STATUS: Record<string, number> = { not_allowed: 403, not_found: 404, invalid: 422, too_large: 413, quota: 429, unavailable: 503, internal: 500 };

/**
 * Reads the bridge's answer. It is always `{ok: true, result}` or `{ok: false, error}`,
 * with HTTP 200 either way: a refusal is an answer. A refusal is thrown as an `ApiError`
 * carrying the server's sentence, so the screen shows it like any other.
 */
export function unwrap<T>(response: unknown): T {
  const reply = readReply(response);
  if (reply.ok) return reply.result as T;
  throw new ApiError(STATUS[reply.error.code] ?? 500, reply.error.message);
}

/** The same route, body and envelope an app's own page is relayed through: one bridge, one set of rules. */
export const bridgeTransport: Transport = async (appId, call, args) => {
  const path = bridgePath(appId, call);
  if (!path) throw new ApiError(0, "That is not something a described app can ask for. Nothing was done.");
  return unwrap(await request<unknown>("POST", path, { body: bridgeBody(call, args) }));
};

export function describedApi(appId: string, transport: Transport = bridgeTransport) {
  const run = <T>(call: string, args: Record<string, unknown>) => transport(appId, call, args) as Promise<T>;
  return {
    list: (args: ListArgs) => run<ListResult>("records.list", args),
    get: (table: string, id: number) => run<OneResult>("records.get", { table, id }),
    create: (table: string, values: Record<string, Value>) => run<OneResult>("records.create", { table, values }),
    update: (table: string, id: number, values: Record<string, Value>) => run<OneResult>("records.update", { table, id, values }),
    remove: (table: string, id: number, unlink = false) => run<{ deleted: number; unlinked: number }>("records.delete", { table, id, unlink }),
  };
}

export type DescribedApi = ReturnType<typeof describedApi>;
