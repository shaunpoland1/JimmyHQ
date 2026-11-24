// The village-wide panels: the break room and the work board.

import type { AgentSummary, BoardPost, RoomLine } from "../api.ts";
import { labelForId } from "./names.ts";
import { toMs } from "./status.ts";

/** "just now", "5 minutes ago", "3 hours ago", "2 days ago". */
export function agoText(ts: number | null | undefined, now: number): string {
  const at = toMs(ts);
  if (at === null) return "some time ago";
  const minutes = Math.max(0, Math.round((now - at) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.round(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

/** Who said it: their name, their id if they are no longer listed, or "Someone". */
export function whoIs(id: string | null | undefined, everyone: readonly Pick<AgentSummary, "id" | "name" | "role">[]): string {
  if (!id) return "Someone";
  return labelForId(id, everyone);
}

/** Break-room lines, newest first. */
export function newestLines(lines: readonly RoomLine[] | undefined): RoomLine[] {
  return [...(lines ?? [])].sort((a, b) => (toMs(b.ts) ?? 0) - (toMs(a.ts) ?? 0) || idNumber(b.id) - idNumber(a.id));
}

function idNumber(id: string): number {
  const n = Number(id.replace(/^\D+/, ""));
  return Number.isFinite(n) ? n : 0;
}

/** A line nobody answered is a note about the room, not something said in it. */
export function wasUnanswered(line: Pick<RoomLine, "kind">): boolean {
  return line.kind === "social_unanswered";
}

const POST_KINDS: Record<string, string> = {
  need_help: "Needs help",
  good_at: "Good at",
  handoff: "Handing over",
};

export function postKindWords(kind: string | null | undefined): string {
  if (!kind) return "Post";
  return POST_KINDS[kind] ?? kind.replace(/_/g, " ");
}

/** Open posts first, each group newest first. */
export function sortPosts(posts: readonly BoardPost[] | undefined): BoardPost[] {
  return [...(posts ?? [])].sort((a, b) => Number(b.open) - Number(a.open) || (toMs(b.ts) ?? 0) - (toMs(a.ts) ?? 0) || idNumber(b.id) - idNumber(a.id));
}

export function boardCounts(posts: readonly BoardPost[] | undefined): { open: number; done: number } {
  const all = posts ?? [];
  const open = all.filter((post) => post.open).length;
  return { open, done: all.length - open };
}
