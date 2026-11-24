// Today: what needs the owner, in the order it is best dealt with. Pure.

import { STUCK_PIECES, draftsToDecide, headline, pageName } from "./lists.js";
import { ideasWaiting, readerTrouble } from "./reader.js";
import { toolLabelOfPost } from "./tools.js";

export const NOTHING = "Nothing needs you right now.";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * The groups of Today, each {key, title, items: [{text, detail, go: {view, piece?, post?}}]},
 * leaving out a group with nothing in it. A group with `guidance: true` only shows the
 * owner what to do next (the first-run hint): nothing waits on them in it, so ``waiting``
 * never counts it. An empty list means nothing needs the owner and no hint is shown.
 */
export function needs(data) {
  const posts = data?.posts ?? [];
  const pieces = data?.pieces ?? [];
  const pages = data?.pages ?? [];
  const groups = [];
  const add = (key, title, items) => {
    if (items.length) groups.push({ key, title, items });
  };

  const broken = readerTrouble(data);  // loud, first: a hand-over that fails must not be quiet
  if (broken) groups.push(broken);

  if (!pages.length) {
    add("pages", "Set up a brand", [{ text: data?.hint || "Add a brand so Posts knows who it writes for.", detail: "", go: { view: "pages" } }]);
    groups[groups.length - 1].guidance = true; // a hint, not something waiting on the owner
  }

  const drafts = draftsToDecide(posts);
  add("drafts", plural(drafts.length, "draft") + " to decide", drafts.map((p) => ({
    text: `${pageName(pages, p.page_id)}: ${headline(p)}`,
    detail: p.questions ? `${plural(p.questions, "question")} open` : p.warnings ? plural(p.warnings, "warning") : "Every check passed",
    go: { view: "drafts", piece: p.piece_id, post: p.id },
  })));

  const unsure = posts.filter((p) => p.state === "unsure");
  add("unsure", "Bookings with no answer", unsure.map((p) => ({
    text: `${pageName(pages, p.page_id)}: ${headline(p)}`,
    detail: `${toolLabelOfPost(data, p)} gave no clear answer. Look there, then say whether it is booked.`,
    go: { view: "queue", post: p.id },
  })));

  const apart = posts.filter((p) => p.out_of_step || p.state === "missing");
  add("out_of_step", "Out of step with the scheduler", apart.map((p) => ({
    text: `${pageName(pages, p.page_id)}: ${headline(p)}`,
    detail: p.note || (p.state === "missing" ? "It is gone from the scheduler before its time." : "A change did not reach the scheduler."),
    go: { view: "queue", post: p.id },
  })));

  const stuck = pieces.filter((p) => STUCK_PIECES.has(p.state));
  add("stuck", "Pieces that stopped", stuck.map((p) => ({
    text: `${pageName(pages, p.page_id)}: ${p.source_title || p.topic || p.source_url || `Piece ${p.id}`}`,
    detail: p.error || p.words,
    go: { view: "drafts", piece: p.id },
  })));

  const untimed = posts.filter((p) => p.state === "queued" && (p.slot_ms === null || p.slot_ms === undefined));
  add("untimed", "Approved, with no time yet", untimed.map((p) => ({
    text: `${pageName(pages, p.page_id)}: ${headline(p)}`,
    detail: p.note || "Choose a time, then book it.",
    go: { view: "queue", post: p.id },
  })));

  const ideas = ideasWaiting(data);
  if (ideas) groups.push(ideas);
  return groups;
}

/**
 * How many things wait on the owner: the lines of every group except guidance. This is the
 * number on the Today tab and on the sidebar; a first run with no page counts nothing.
 */
export function waiting(groups) {
  return (groups ?? []).filter((g) => !g.guidance).reduce((n, g) => n + g.items.length, 0);
}

// The steps that ask a model (or an agent that has one), so a long wait in them may mean none can answer.
const MODEL_STEPS = new Set(["writing", "captioning", "revising", "in_check", "analysing"]);
const STEP_WHY = {
  writing: "drafting", captioning: "writing the caption", revising: "revising the draft", in_check: "checking the draft", analysing: "looking at the pictures",
};

/**
 * Pieces that have sat in one model step longer than Posts waits for a step (`slow_minutes`),
 * each as {text, detail}: what is stuck and the likely reason, so "Nothing needs you" is
 * never said over a piece that cannot move. Posts cannot see inside an agent, so the reason
 * is what it can know: a model must be set up for the role, and an agent must have one.
 */
export function stuckSteps(data) {
  const now = Number(data?.now_ms);
  const limit = (Number(data?.slow_minutes) || 30) * 60_000;
  const pages = data?.pages ?? [];
  return (data?.pieces ?? [])
    .filter((p) => MODEL_STEPS.has(p.state) && Number.isFinite(now) && Number.isFinite(Number(p.updated)) && now - Number(p.updated) > limit)
    .map((p) => {
      const minutes = Math.round((now - Number(p.updated)) / 60_000);
      const waited = minutes >= 120 ? `${Math.round(minutes / 60)} hours` : minutes === 1 ? "1 minute" : `${minutes} minutes`;
      return {
        text: `${pageName(pages, p.page_id)}: ${p.source_title || p.topic || p.source_url || `Piece ${p.id}`} has been ${STEP_WHY[p.state]} for ${waited} and has not moved.`,
        detail: "No model has answered. Check that a model is set up under Settings, Models and roles, and that any agent holding a Posts role has one under Village. Then press Retry, or choose Finish without agents.",
        go: { view: "drafts", piece: p.id },
      };
    });
}

/** One quiet line on what is under way and needs nothing: "2 pieces being drafted." */
export function underWay(data) {
  const working = (data?.pieces ?? []).filter((p) => !STUCK_PIECES.has(p.state) && !["ready", "closed", "dropped"].includes(p.state));
  if (!working.length) return "";
  return `${plural(working.length, "piece")} being worked on.`;
}
