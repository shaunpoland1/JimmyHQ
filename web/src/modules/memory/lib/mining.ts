// Mining in plain words: why a proposed fact was dropped or an item skipped,
// what a pass did with what it read, where the two confidence thresholds sit,
// and which live events mean the mining panels should read again.

import type { FlatCounts, MineCounts, MineResult, Mining, Source, Tally } from "../api.ts";

// ---- reasons ----

/** Why a proposed fact was thrown away, as the end of "dropped: …". */
const DROP_WORDS: Record<string, string> = {
  duplicate: "already known",
  declined: "you said no before",
  no_evidence: "no evidence",
  not_from_owner: "not your words",
  secret: "looked like a secret",
  too_long: "too long",
  invalid: "not a usable statement",
  too_many: "more than can be read from one answer",
};

/** Why an item was passed over without being shown to the model. */
const SKIP_WORDS: Record<string, string> = {
  archived: "in an archived conversation",
  not_owner: "not written by you",
  incomplete: "a reply that did not finish",
  off_record_question: "answered a question asked off the record",
  off_record_context: "a reply written after the conversation had gone off the record",
  gone: "no longer there",
  own_card: "one of memory's own cards",
  empty: "nothing in it",
  too_long: "too long",
  instruction: "read like an instruction to the model",
  unreadable: "could not be read",
  model_failed: "the model failed on it three times",
};

/** A dropped fact's reason in plain words. One the screen does not know is shown by its raw name. */
export function dropReasonWords(reason: string): string {
  return DROP_WORDS[reason] ?? reason;
}

/** A skipped item's reason in plain words. One the screen does not know is shown by its raw name. */
export function skipReasonWords(reason: string): string {
  return SKIP_WORDS[reason] ?? reason;
}

// ---- tallies ----

function whole(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function sum(counts: Record<string, number>): number {
  return Object.values(counts).reduce((total, n) => total + whole(n), 0);
}

export const EMPTY_TALLY: Tally = { read: 0, skipped: {}, skipped_total: 0, proposed: 0, saved: 0, sent: 0, ignored: 0, dropped: {}, dropped_total: 0 };

/**
 * A tally from the flat counts of `POST /mine` and of a timeline entry:
 * `dropped.<reason>` and `skipped.<reason>` become the two maps, and a bare
 * `skipped` (their sum) is used only when no reason came with it.
 */
export function tallyFromFlat(flat: Record<string, unknown>): Tally {
  const skipped: Record<string, number> = {};
  const dropped: Record<string, number> = {};
  for (const [name, value] of Object.entries(flat)) {
    const dot = name.indexOf(".");
    if (dot <= 0) continue;
    const head = name.slice(0, dot);
    const tail = name.slice(dot + 1);
    if (!tail) continue;
    if (head === "skipped") skipped[tail] = whole(value);
    else if (head === "dropped") dropped[tail] = whole(value);
  }
  return {
    read: whole(flat.read),
    skipped,
    skipped_total: Math.max(sum(skipped), whole(flat.skipped)),
    proposed: whole(flat.proposed),
    saved: whole(flat.saved),
    sent: whole(flat.sent),
    ignored: whole(flat.ignored),
    dropped,
    dropped_total: sum(dropped),
  };
}

/** One source's counts from `POST /mine`, with the two totals worked out. */
export function tallyFromCounts(counts: MineCounts): Tally {
  return {
    read: whole(counts.read),
    skipped: counts.skipped ?? {},
    skipped_total: sum(counts.skipped ?? {}),
    proposed: whole(counts.proposed),
    saved: whole(counts.saved),
    sent: whole(counts.sent),
    ignored: whole(counts.ignored),
    dropped: counts.dropped ?? {},
    dropped_total: sum(counts.dropped ?? {}),
  };
}

export type ReasonCount = { reason: string; words: string; n: number };

function reasonCounts(counts: Record<string, number> | undefined, words: (reason: string) => string): ReasonCount[] {
  return Object.entries(counts ?? {})
    .map(([reason, n]) => ({ reason, words: words(reason), n: whole(n) }))
    .filter((item) => item.n > 0)
    .sort((a, b) => b.n - a.n || a.reason.localeCompare(b.reason));
}

export type TallyBreakdown = {
  read: number;
  proposed: number;
  saved: number;
  sent: number;
  ignored: number;
  dropped: number;
  /** Each reason a fact was dropped for, most frequent first, zeroes left out. */
  droppedBy: ReasonCount[];
  skipped: number;
  skippedBy: ReasonCount[];
  /**
   * proposed minus everything it should add up to. 0 when the numbers agree;
   * anything else is shown, never hidden, because it means the counts are off.
   */
  unaccounted: number;
  /** Nothing was read, skipped or proposed. */
  nothing: boolean;
};

/**
 * What a tally says, ready to draw. The totals are worked out from the reasons
 * themselves, so a reason this screen has never heard of is still counted.
 */
export function breakdown(tally: Tally): TallyBreakdown {
  const droppedBy = reasonCounts(tally.dropped, dropReasonWords);
  const skippedBy = reasonCounts(tally.skipped, skipReasonWords);
  const dropped = Math.max(droppedBy.reduce((total, item) => total + item.n, 0), whole(tally.dropped_total));
  const skipped = Math.max(skippedBy.reduce((total, item) => total + item.n, 0), whole(tally.skipped_total));
  const read = whole(tally.read);
  const proposed = whole(tally.proposed);
  const saved = whole(tally.saved);
  const sent = whole(tally.sent);
  const ignored = whole(tally.ignored);
  return {
    read,
    proposed,
    saved,
    sent,
    ignored,
    dropped,
    droppedBy,
    skipped,
    skippedBy,
    unaccounted: proposed - (saved + sent + ignored + dropped),
    nothing: read === 0 && proposed === 0 && skipped === 0,
  };
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function reasonList(items: readonly ReasonCount[]): string {
  return items.map((item) => `${item.words} ${item.n}`).join(", ");
}

/** "2 saved automatically, 2 sent to the Bench, 1 ignored, 2 dropped (no evidence 1, looked like a secret 1)". */
export function outcomesLine(b: TallyBreakdown): string {
  const parts: string[] = [];
  if (b.saved > 0) parts.push(`${b.saved} saved automatically`);
  if (b.sent > 0) parts.push(`${b.sent} sent to the Bench`);
  if (b.ignored > 0) parts.push(`${b.ignored} ignored`);
  if (b.dropped > 0) parts.push(`${b.dropped} dropped${b.droppedBy.length ? ` (${reasonList(b.droppedBy)})` : ""}`);
  if (b.unaccounted !== 0) parts.push(`${Math.abs(b.unaccounted)} ${b.unaccounted > 0 ? "not accounted for" : "more than were proposed"}`);
  return parts.join(", ");
}

/** A tally as one or two sentences, for the result of "Mine now" and for the timeline. */
export function tallySentence(tally: Tally): string {
  const b = breakdown(tally);
  if (b.nothing) return "There was nothing new to read.";
  const sentences = [`Read ${plural(b.read, "item", "items")}.`];
  if (b.skipped > 0) sentences.push(`Passed over ${b.skipped}${b.skippedBy.length ? ` (${reasonList(b.skippedBy)})` : ""}.`);
  if (b.proposed === 0) sentences.push("No fact was proposed.");
  else sentences.push(`${plural(b.proposed, "fact", "facts")} proposed: ${outcomesLine(b)}.`);
  return sentences.join(" ");
}

export type MineOutcome = {
  ok: boolean;
  /** The headline: what the pass did, or the server's reason it could not run. */
  line: string;
  /** One line per source that reported an error, in the server's words. */
  errors: { source: string; message: string }[];
};

/** What "Mine now" came back with. `labels` maps a source id to its name. */
export function describeMine(result: MineResult, labels: Record<string, string> = {}): MineOutcome {
  if (!result.ran) return { ok: false, line: result.reason ?? "The pass could not run, and the server gave no reason.", errors: [] };
  const errors = Object.entries(result.sources ?? {})
    .filter(([, counts]) => typeof counts.error === "string" && counts.error !== "")
    .map(([source, counts]) => ({ source: labels[source] ?? source, message: counts.error ?? "" }));
  const filed = whole(result.filed);
  const cards = filed > 0 ? ` ${plural(filed, "card", "cards")} filed on the Bench.` : "";
  return { ok: errors.length === 0, line: `${tallySentence(tallyFromFlat(result.totals ?? {}))}${cards}`, errors };
}

// ---- the thresholds ----

export type ScaleBand = {
  id: "ignored" | "bench" | "saved";
  label: string;
  /** Where the band starts and ends on the 0 to 1 scale. */
  from: number;
  to: number;
  /** Its width as a percentage of the scale, 0 when the band is empty. */
  percent: number;
};

export type ThresholdScale = {
  bands: ScaleBand[];
  /** The two marks, as percentages from the left. `auto` is null when nothing is ever saved without asking. */
  marks: { ignore: number; auto: number | null };
  /** The whole scale in one sentence, for a screen reader and for the caption. */
  words: string;
};

function clamp01(value: number): number {
  return Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0;
}

function shown(value: number): string {
  return (Math.round(value * 100) / 100).toString();
}

/**
 * Where the two thresholds sit between 0 and 1: ignored below the first, the
 * Bench between, saved automatically from the second. A second threshold above
 * 1 means nothing is saved without asking; a first of 0 ignores nothing.
 */
export function thresholdScale(ignoreBelow: number, autoSaveAbove: number): ThresholdScale {
  const low = clamp01(ignoreBelow);
  const never = !Number.isFinite(autoSaveAbove) || autoSaveAbove > 1;
  // They can meet but never cross; drawn that way even if a bad pair arrives.
  const high = never ? 1 : Math.max(low, clamp01(autoSaveAbove));
  const width = (from: number, to: number) => Math.round((to - from) * 1000) / 10;
  const bands: ScaleBand[] = [
    { id: "ignored", label: "Ignored", from: 0, to: low, percent: width(0, low) },
    { id: "bench", label: "Bench", from: low, to: high, percent: width(low, high) },
    { id: "saved", label: "Saved automatically", from: high, to: 1, percent: never ? 0 : width(high, 1) },
  ];
  const parts = [
    low > 0 ? `ignored below ${shown(low)}` : "nothing is ignored",
    high > low ? `asked on the Bench from ${shown(low)}${never ? "" : ` up to ${shown(high)}`}` : "nothing is sent to the Bench",
    never ? "nothing is saved without you" : `saved automatically from ${shown(high)}`,
  ];
  const sentence = parts.join(", ");
  return {
    bands,
    marks: { ignore: Math.round(low * 1000) / 10, auto: never ? null : Math.round(high * 1000) / 10 },
    words: `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`,
  };
}

// ---- the overall line ----

export type SettingsSection = "memory" | "models";

/**
 * Which settings sections to link beside the server's reason. The reason names
 * them itself ("under Settings, Models"); with no such words, Memory.
 */
export function sectionsFor(reason: string | null): SettingsSection[] {
  if (!reason) return [];
  const out: SettingsSection[] = [];
  if (/Settings, Models/i.test(reason)) out.push("models");
  if (/Settings, Memory/i.test(reason)) out.push("memory");
  return out.length ? out : ["memory"];
}

/** Whether mining is happening, in a few words. */
export function miningHeadline(mining: Pick<Mining, "on" | "working">): string {
  if (mining.working) return "Mining is working";
  return mining.on ? "Mining is not happening" : "Mining is switched off";
}

/** Where the items go to be read. */
export function modelPlace(model: Mining["model"]): string {
  if (model.name === null) return "No model can be asked";
  if (model.is_local === true) return "runs on your own machines";
  if (model.is_local === false) return "hosted: what is read leaves this machine";
  return "";
}

/** "2 of 5 Bench cards open", and how many more wait for a slot. */
export function benchLine(mining: Pick<Mining, "open_cards" | "max_open_cards" | "waiting_for_bench">): string {
  const open = `${whole(mining.open_cards)} of ${whole(mining.max_open_cards)} Bench cards open`;
  const waiting = whole(mining.waiting_for_bench);
  return waiting > 0 ? `${open}, ${waiting} more waiting for a slot` : open;
}

/** "12 waiting", "at least 20 waiting", or null when the worker has not counted yet. */
export function waitingWords(source: Pick<Source, "items_waiting" | "items_waiting_more">): string | null {
  if (source.items_waiting === null || source.items_waiting === undefined) return null;
  const n = whole(source.items_waiting);
  if (n === 0 && !source.items_waiting_more) return "none waiting";
  return `${source.items_waiting_more ? "at least " : ""}${n} waiting`;
}

/** Why a source is or is not being read, in a few words. */
export function sourceState(source: Pick<Source, "on">, mining: Pick<Mining, "working" | "on">): { reading: boolean; words: string } {
  if (!source.on) return { reading: false, words: "Off: not read" };
  if (!mining.working) return { reading: false, words: mining.on ? "On, but mining is not happening" : "On, but mining is switched off" };
  return { reading: true, words: "Being read" };
}

/** A source's switch, shown at once while the setting is saved. */
export function withSourceSwitched<T extends { sources: Source[] }>(data: T, id: string, on: boolean): T {
  return { ...data, sources: data.sources.map((source) => (source.id === id ? { ...source, on } : source)) };
}

/** Source ids to their names, for places that only hold the id. */
export function sourceLabels(sources: readonly Pick<Source, "id" | "label">[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const source of sources ?? []) out[source.id] = source.label;
  return out;
}

// ---- staying current ----

type LiveEvent = { kind: string; payload: Record<string, unknown> };

/**
 * Whether a live event means the mining panels (sources, proposed facts, the
 * timeline) should read again: any memory event but a search receipt, and a
 * Bench event about one of memory's own cards.
 */
export function touchesMining(event: LiveEvent): boolean {
  if (event.kind.startsWith("memory.")) return event.kind !== "memory.recalled";
  if (!event.kind.startsWith("bench.")) return false;
  const { card_kind: cardKind, kind, subject } = event.payload;
  return cardKind === "memory.candidate" || kind === "memory.candidate" || (typeof subject === "string" && subject.startsWith("memory_candidate:"));
}

/**
 * Whether a live event can change what the sources panel says without touching
 * memory itself: one of memory's settings (a switch, a threshold, the role), or
 * the models a role can ask.
 */
export function touchesSources(event: LiveEvent): boolean {
  if (event.kind === "setting.updated" || event.kind === "setting.reset") {
    const { key } = event.payload;
    return typeof key === "string" && key.startsWith("memory.");
  }
  return event.kind.startsWith("model.role.") || event.kind.startsWith("model.provider.");
}

export type { FlatCounts };
