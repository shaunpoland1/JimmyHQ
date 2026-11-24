// The agent's event log in plain words: which kinds belong to which filter, what each
// event says, and how pages of older events are joined and asked for.

import type { Need, VillageEvent } from "../api.ts";
import { NEED_LABELS } from "./needs.ts";

export const GROUPS = ["everything", "actions", "refusals", "feelings", "talk", "model", "night"] as const;
export type Group = (typeof GROUPS)[number];

export const GROUP_LABELS: Record<Group, string> = {
  everything: "Everything",
  actions: "Actions",
  refusals: "Refusals",
  feelings: "Feelings and needs",
  talk: "Talk",
  model: "Model calls",
  night: "Night and sleep",
};

const SPECIFIC: Record<Exclude<Group, "everything">, readonly string[]> = {
  actions: [
    "action",
    "task_done",
    "task_taken",
    "own_task",
    "task_reopened",
    "set_aside",
    "resume",
    "bench_submitted",
    "bench_updated",
    "bench_response",
    "bench_decided",
    "work_final",
    "work_reviewed",
    "handover_queued",
    "finding_queued",
    "finding_dropped",
  ],
  refusals: ["validator_reject", "claim_unverified", "date_slip"],
  feelings: ["need_change", "feeling", "mode_change"],
  talk: ["chat", "message", "breakroom", "social_unanswered", "board_post", "board_resolved"],
  model: ["model_call", "thought", "brain_error", "platform_fault"],
  night: ["sleep", "wake", "consolidation", "memory_rollback"],
};

/** Kinds that are in "Everything" but in no one filter. A tick, which fires every minute, is left out of all of them. */
const LIFE = ["born", "retired", "world_paused", "resume_note_written"] as const;

/** The kinds to ask the server for. "Everything" is every kind this screen can word, minus ticks. */
export function kindsOf(group: Group): readonly string[] {
  if (group !== "everything") return SPECIFIC[group];
  return [...Object.values(SPECIFIC).flat(), ...LIFE];
}

/** Which filter an event belongs to, or null for a kind in none (a tick). */
export function groupOf(kind: string): Exclude<Group, "everything"> | null {
  for (const [group, kinds] of Object.entries(SPECIFIC)) if (kinds.includes(kind)) return group as Exclude<Group, "everything">;
  return null;
}

export const PAGE = 100;

// ---- paging ----

/** The number in an event id like "evt-12"; 0 when it is not one. */
export function eventNumber(id: string): number {
  const match = /^evt-(\d+)$/.exec(id);
  return match ? Number(match[1]) : 0;
}

/** Pages of events as one list, newest first, each event once. Pages overlap when the newest page is read again. */
export function mergeEvents(pages: readonly (readonly VillageEvent[])[]): VillageEvent[] {
  const byId = new Map<string, VillageEvent>();
  for (const page of pages) for (const event of page) byId.set(event.id, event);
  return [...byId.values()].sort((a, b) => eventNumber(b.id) - eventNumber(a.id));
}

/** The `before` to ask the next older page with: the oldest event held. Undefined when nothing is held. */
export function olderEdge(events: readonly VillageEvent[]): string | undefined {
  let oldest: VillageEvent | undefined;
  for (const event of events) if (!oldest || eventNumber(event.id) < eventNumber(oldest.id)) oldest = event;
  return oldest?.id;
}

/** A full page means there may be more behind it; a short page is the end of the log. */
export function mayHaveMore(lastPageSize: number, asked: number = PAGE): boolean {
  return lastPageSize >= asked;
}

export type DayGroup<T> = { day: string; items: T[] };

/** Items grouped under the day they happened, keeping their order. */
export function groupByDayOf<T extends { ts: number }>(items: readonly T[], dayOf: (ts: number) => string): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  for (const item of items) {
    const day = dayOf(item.ts);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.items.push(item);
    else groups.push({ day, items: [item] });
  }
  return groups;
}

// ---- wording ----

export type Tone = "normal" | "refusal" | "warn" | "quiet";

export type Described = {
  group: Exclude<Group, "everything"> | null;
  headline: string;
  /** A second line: the reason, the result, the words said. */
  detail: string | null;
  tone: Tone;
};

type Names = (id: string | null | undefined) => string;

const MODE_WORDS: Record<string, string> = {
  work: "working",
  explore: "exploring",
  outlet: "taking a break",
  social: "in the break room",
  rest: "resting",
  sleep: "asleep",
};

const CAUSE_WORDS: Record<string, string> = {
  sleep: "sleeping",
  resting: "resting",
  validator_reject: "a refusal",
  verified_completion: "finishing something for real",
  small_win: "a small win",
  conversation: "a conversation",
  submitted_to_bench: "handing something to the Bench",
  acknowledged: "being acknowledged",
  bench_approved: "you approving its work",
};

const BOARD_KINDS: Record<string, string> = { need_help: "asking for help", good_at: "saying what it is good at", handoff: "handing something over" };

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
}

function clip(value: string, most = 400): string {
  return value.length > most ? `${value.slice(0, most).trimEnd()}…` : value;
}

function words(value: string): string {
  return value.replace(/[_-]+/g, " ").trim();
}

function sentence(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

function quoted(value: string): string {
  return value ? `"${clip(value, 120)}"` : "";
}

function causeWords(cause: unknown): string {
  const key = text(cause);
  return CAUSE_WORDS[key] ?? words(key);
}

/** One event as a plain sentence, with the reason or the words behind it, and how loudly to show it. */
export function describeEvent(event: Pick<VillageEvent, "kind" | "data">, nameOf: Names = (id) => id ?? "someone"): Described {
  const d = event.data ?? {};
  const group = groupOf(event.kind);
  const said = (headline: string, detail: string | null = null, tone: Tone = "normal"): Described => ({ group, headline, detail, tone });

  switch (event.kind) {
    case "action": {
      const tool = words(text(d.tool) || "a tool");
      const task = text(d.task);
      const result = text(d.result);
      if (d.ok === false) return said(`Tried ${tool} and it did not work`, [task ? `For: ${task}` : "", result].filter(Boolean).join(". ") || null, "warn");
      const extra = d.no_new_result ? " and got nothing new back" : "";
      return said(`Used ${tool}${extra}`, [task ? `For: ${task}` : "", clip(result, 200)].filter(Boolean).join(". ") || null, d.no_new_result ? "quiet" : "normal");
    }
    case "validator_reject": {
      const tool = words(text(d.tool) || "something");
      return said(`Was refused when it tried ${tool}`, text(d.reason) || "No reason was recorded.", "refusal");
    }
    case "claim_unverified":
      return said(`Said it had finished ${quoted(text(d.task)) || "a task"} but had nothing to show for it`, "A task only counts as finished when the agent points at what it saw. This one was not counted.", "refusal");
    case "date_slip":
      return said(`Wrote a date that did not match the calendar${text(d.tool) ? ` (in ${words(text(d.tool))})` : ""}`, text(d.title) ? `Called: ${text(d.title)}` : null, "refusal");
    case "need_change": {
      const need = text(d.need) as Need;
      const label = NEED_LABELS[need] ?? sentence(words(text(d.need) || "A need"));
      const delta = typeof d.delta === "number" ? d.delta : 0;
      const value = typeof d.value === "number" ? Math.round(d.value) : null;
      const cause = causeWords(d.cause);
      const move = delta >= 0 ? "rose" : "fell";
      return said(`${label} ${move}${value !== null ? ` to ${value}` : ""}`, cause ? `Because of ${cause}.` : null, "quiet");
    }
    case "feeling":
      return said(`Felt ${words(text(d.phrase) || "something")}`, null);
    case "mode_change": {
      const from = MODE_WORDS[text(d.old)] ?? words(text(d.old));
      const to = MODE_WORDS[text(d.new)] ?? words(text(d.new));
      return said(`Went from ${from || "its last state"} to ${to || "a new one"}`, text(d.cause) ? sentence(`${text(d.cause)}.`) : null, "quiet");
    }
    case "sleep": {
      const kind = text(d.sleep_kind);
      return said(kind === "nap" ? "Took a nap" : kind === "forced" ? "Fell asleep: it ran out of energy" : "Went to sleep for the night", null);
    }
    case "wake":
      return said(text(d.cause) === "urgent_call" ? "Was woken by your urgent message" : "Woke up", null);
    case "consolidation": {
      const nap = Boolean(d.nap);
      if (d.failed) return said(`${nap ? "Its nap" : "Its night"} did not finish sorting memory`, text(d.why) || null, "warn");
      const ops = typeof d.ops === "number" ? d.ops : 0;
      return said(`${nap ? "Sorted a nap's worth into memory" : "Sorted the day into memory"}: ${ops} ${ops === 1 ? "change" : "changes"}${d.dream ? ", and dreamt" : ""}`, text(d.notes) || null);
    }
    case "memory_rollback":
      return said(`Its memory was rolled back to snapshot ${text(d.to)}`, text(d.note) || null, "warn");
    case "born":
      return said("Was born", null);
    case "retired":
      return said("Retired", text(d.note) || null);
    case "world_paused":
      return said("The village was not running for a while", typeof d.minutes === "number" ? `About ${Math.round(d.minutes)} minutes passed without it living through them.` : null, "quiet");
    case "chat": {
      const owner = text(d.sender) === "owner";
      return said(owner ? "You wrote to it" : "It wrote to you", clip(text(d.text)));
    }
    case "message": {
      const other = nameOf(text(d.other) || null);
      return said(d.heard ? `Was spoken to by ${other}` : `Spoke to ${other}`, d.venting ? "It was letting off steam." : null);
    }
    case "breakroom":
      return said("Said in the break room", clip(text(d.text)));
    case "social_unanswered":
      return said("Nobody answered in the break room", "It went back to other things for a while.", "quiet");
    case "board_post":
      return said(`Posted to the work board, ${BOARD_KINDS[text(d.post_kind)] ?? "a note"}`, clip(text(d.text)));
    case "board_resolved":
      return said("Marked a work-board post as dealt with", text(d.note) || null);
    case "model_call": {
      if (d.slip) return said(`The model's answer was ${words(text(d.slip))}`, d.retry ? "This was a retry." : null, "warn");
      const ms = typeof d.ms === "number" ? `${(d.ms / 1000).toFixed(1)} seconds` : null;
      const tokens = typeof d.input_tokens === "number" && typeof d.output_tokens === "number" ? `${d.input_tokens} tokens in, ${d.output_tokens} out` : null;
      return said(`Asked the model${text(d.mode) ? ` (${words(text(d.mode))})` : ""}`, [text(d.model), ms, tokens].filter(Boolean).join(" · ") || null, "quiet");
    }
    case "thought":
      return said(d.cut_off ? "Its thought was cut off" : "Thought", clip(text(d.text)) || null, d.cut_off ? "warn" : "quiet");
    case "brain_error":
      return said("The model call failed", text(d.detail) || text(d.what) || null, "warn");
    case "platform_fault":
      return said(`A fault: ${text(d.what) || "something went wrong"}`, text(d.detail) || null, "warn");
    case "task_done":
      return said(`Finished ${quoted(text(d.task)) || "a task"}`, null);
    case "task_taken":
      return said(`Took on ${quoted(text(d.title) || text(d.task)) || "a task"}`, null);
    case "own_task":
      return said(`Chose its own task: ${quoted(text(d.title) || text(d.task)) || "a task"}`, null);
    case "task_reopened":
      return said(`Reopened ${quoted(text(d.title) || text(d.task)) || "a task"}`, null);
    case "set_aside":
      return said(`Set aside ${quoted(text(d.task)) || "its task"}`, "It was too frustrated to carry on. It comes back to it once it is calm.");
    case "resume":
      return said(`Picked ${quoted(text(d.task)) || "its task"} back up`, d.chosen ? "By its own choice." : null);
    case "resume_note_written":
      return said(`Wrote itself a note for ${quoted(text(d.task)) || "a task"}`, clip(text(d.note)) || null, "quiet");
    case "bench_submitted":
      return said(`Handed ${quoted(text(d.title)) || "something"} to the Bench`, "It waits for your answer.");
    case "bench_updated":
      return said(`Updated ${quoted(text(d.title)) || "a card"} on the Bench`, null);
    case "bench_response":
      return said("Heard back from the Bench", text(d.verdict) ? sentence(words(text(d.verdict))) : null);
    case "bench_decided":
      return said(`You ${words(text(d.decision) || "decided")} ${quoted(text(d.title)) || "a card"}`, text(d.note) || null);
    case "work_final":
      return said(`${quoted(text(d.title)) || "Its work"} was marked final`, null);
    case "work_reviewed":
      return said("Its work was reviewed", text(d.path) || null);
    case "handover_queued":
      return said(`Queued ${quoted(text(d.title)) || "a handover"}`, null);
    case "finding_queued":
      return said(`Queued a finding: ${quoted(text(d.title)) || "untitled"}`, null);
    case "finding_dropped":
      return said(`Dropped a finding: ${quoted(text(d.title)) || "untitled"}`, null, "quiet");
    default:
      return { group, headline: sentence(words(event.kind) || "An event"), detail: null, tone: "quiet" };
  }
}

/** How many of these events are refusals. */
export function refusalCount(events: readonly Pick<VillageEvent, "kind">[]): number {
  return events.filter((event) => groupOf(event.kind) === "refusals").length;
}

/** A refusal count over the last day from the agent's card, as a sentence; null when there were none. */
export function refusalsToday(counts: Record<string, number> | undefined): string | null {
  const total = (counts?.validator_reject ?? 0) + (counts?.claim_unverified ?? 0) + (counts?.date_slip ?? 0);
  if (total === 0) return null;
  return `${total} ${total === 1 ? "refusal" : "refusals"} in the last 24 hours. A run of them means it is struggling with something.`;
}

/** The raw data behind an event, for the expander. */
export function rawData(event: Pick<VillageEvent, "id" | "kind" | "ts" | "data">): string {
  return JSON.stringify({ id: event.id, kind: event.kind, ts: event.ts, data: event.data }, null, 2);
}
