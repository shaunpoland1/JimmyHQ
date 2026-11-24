// What each tile says, worked out from the other modules' own responses, and
// which of them a live Tape event makes stale.

import type { Board } from "../../bench/api.ts";
import type { HealthResponse, UsageResponse, UsageWindow } from "../../models/api.ts";
import { formatCompact, formatCost } from "../../models/lib/format.ts";
import type { TaskBoard } from "../../tasks/api.ts";
import { todayList } from "../../tasks/lib/group.ts";

/** The pieces of server data the Wall reads. */
export type WallPart = "settings" | "usage" | "tape" | "bench" | "today" | "models";

function settingKey(payload: Record<string, unknown>): string {
  return typeof payload.key === "string" ? payload.key : "";
}

/**
 * The parts to read again after an event of this kind. Every event changes
 * the Tape's size, so "tape" is always among them.
 */
export function staleAfter(kind: string, payload: Record<string, unknown> = {}): WallPart[] {
  const parts: WallPart[] = ["tape"];
  if (kind.startsWith("task.")) parts.push("today");
  else if (kind.startsWith("bench.")) parts.push("bench");
  else if (kind === "model.called" || kind === "model.failed") parts.push("usage", "models");
  else if (kind === "model.prices.set") parts.push("usage");
  else if (kind.startsWith("model.provider.")) parts.push("models");
  else if (kind === "setting.updated" || kind === "setting.reset") {
    const key = settingKey(payload);
    parts.push("settings");
    if (key.startsWith("tasks.") || key === "owner.timezone") parts.push("today");
    else if (key.startsWith("bench.")) parts.push("bench");
    else if (key.startsWith("models.")) parts.push("usage", "models");
  }
  return parts;
}

// ---- bench ----

export type Tally = { name: string; count: number };

export type BenchSummary = {
  needs: number;
  /** Most first, then by name. */
  kinds: Tally[];
  sources: Tally[];
  /** When the longest-waiting card began to need the owner; null when none does. */
  oldestSince: number | null;
};

function tallies(counts: Record<string, number>): Tally[] {
  return Object.entries(counts)
    .filter(([, count]) => count > 0)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

export function benchSummary(board: Pick<Board, "counts" | "needs_by_kind" | "needs_by_source" | "oldest_needs_since">): BenchSummary {
  return {
    needs: board.counts.needs,
    kinds: tallies(board.needs_by_kind),
    sources: tallies(board.needs_by_source),
    oldestSince: board.counts.needs > 0 ? board.oldest_needs_since : null,
  };
}

/** How long the oldest card has waited at `now`; never negative, whatever the two clocks say. */
export function waitedMs(oldestSince: number | null, now: number): number | null {
  return oldestSince === null ? null : Math.max(0, now - oldestSince);
}

// ---- today ----

export type TaskLine = {
  id: string;
  title: string;
  /** Ticked: finished today, or this round kept today. */
  done: boolean;
  /** Days past its date; 0 when on time or ticked. */
  lapsedDays: number;
  /** "HH:MM" when it has a time today and is not ticked. */
  time: string | null;
};

export type TodaySummary = { lines: TaskLine[]; done: number; total: number };

/** Today's checklist: what is still to do first (lapsed included), then what is ticked. */
export function todaySummary(board: Pick<TaskBoard, "today" | "kept_today" | "done" | "date" | "timezone">): TodaySummary {
  const list = todayList(board);
  const lines = list.items.map(({ task, state }) => {
    const done = state !== "due";
    return {
      id: task.id,
      title: task.title,
      done,
      lapsedDays: done ? 0 : Math.max(0, task.lapsed_days),
      time: done ? null : task.due_time,
    };
  });
  return { lines, done: list.done, total: list.total };
}

/** "2 of 5 done" */
export function progressText(done: number, total: number): string {
  return `${done} of ${total} done`;
}

/** "+3 more" */
export function moreText(count: number): string {
  return `+${count} more`;
}

// ---- usage ----

export type UsageRow = {
  id: "calls" | "tokens" | "cost" | "saved";
  label: string;
  /** The same thing said in full, for a screen reader. */
  name: string;
  day: string;
  all: string;
};

const NONE = "–";

function windowCells(window: UsageWindow, currency: string, locale?: string): Record<UsageRow["id"], string> {
  return {
    calls: formatCompact(window.calls, locale),
    tokens: formatCompact(window.input_tokens + window.output_tokens, locale),
    cost: formatCost(window.estimated_cost, currency, locale),
    saved: window.saved_by_local === null ? NONE : formatCost(window.saved_by_local, currency, locale),
  };
}

/** Calls, tokens, estimated cost and what running locally saved: the last 24 hours beside all time. */
export function usageRows(usage: Pick<UsageResponse, "last_24h" | "all_time" | "currency">, locale?: string): UsageRow[] {
  const day = windowCells(usage.last_24h, usage.currency, locale);
  const all = windowCells(usage.all_time, usage.currency, locale);
  const row = (id: UsageRow["id"], label: string, name: string): UsageRow => ({ id, label, name, day: day[id], all: all[id] });
  return [
    row("calls", "calls", "Model calls"),
    row("tokens", "tokens", "Tokens"),
    row("cost", "est. cost", "Estimated cost"),
    row("saved", "saved", "Saved by running locally"),
  ];
}

// ---- models ----

export type ProviderState = "online" | "offline" | "unchecked";
export type ProviderLine = { id: string; label: string; state: ProviderState; text: string };

const STATE_TEXT: Record<ProviderState, string> = { online: "online", offline: "offline", unchecked: "not checked" };

/** One line per enabled provider, in the server's order. */
export function providerLines(health: Pick<HealthResponse, "providers">): ProviderLine[] {
  return health.providers.map((entry) => {
    const state: ProviderState = entry.ok === null ? "unchecked" : entry.ok ? "online" : "offline";
    // Never checked but with something to say: it needs a key and has none.
    const text = entry.ok === null && entry.error ? "not set up" : STATE_TEXT[state];
    return { id: entry.id, label: entry.label || entry.id, state, text };
  });
}

/** "all online", "1 of 3 offline", or "" when there is nothing to sum up. */
export function providersText(lines: readonly ProviderLine[]): string {
  if (lines.length === 0) return "";
  const offline = lines.filter((line) => line.state === "offline").length;
  if (offline > 0) return `${offline} of ${lines.length} offline`;
  return lines.every((line) => line.state === "online") ? "all online" : "";
}
