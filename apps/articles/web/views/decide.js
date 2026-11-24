// What the owner decides on an article: Approve, Edit, Send back (with a note), Decline
// (with a reason), Drop, Retry, Write anyway, and Yes / No / Fixed on a question; and for
// an article already sent, Edit and send again. Each is one call to the app; a refusal is
// shown as the app's own sentence.

import { checkSummary } from "../lib/evidence.js";
import { DROP_ARTICLE, askSafely } from "../lib/danger.js";
import { declineReason } from "../lib/forms.js";
import { canEditSent, editSentTone, editSentWords } from "../lib/sent.js";
import { ask, dialog, field, h, say, why } from "../_ui.js";
import { editArticle } from "./editor.js";

const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** The article open now, when it is waiting for the owner's decision. */
function waiting(ctx) {
  const d = ctx.state.detail;
  return d && d.piece.state === "ready" && d.revision ? d : null;
}

/** After a decision: the next article to decide, or the list. */
async function decided(ctx, words) {
  say(words, "ok");
  await ctx.refresh();
  const next = ctx.decideList();
  if (next.length) await ctx.open(next[Math.min(Math.max(ctx.state.selected, 0), next.length - 1)].id);
}

async function run(ctx, call, args, words) {
  try {
    const done = await ctx.call(call, args);
    say(done?.said || words, "ok");
    await ctx.refresh();
    return done;
  } catch (error) {
    say(why(error), "bad");
    return null;
  }
}

export async function approve(ctx) {
  const d = waiting(ctx);
  if (!d) return;
  const open = d.checks.filter((c) => c.level === "ask" && !c.answer).length;
  if (open && !(await ask("Approve with questions open?", `${plural(open, "question")} on this article ${open === 1 ? "is" : "are"} not answered. You can approve anyway; the article keeps what the checks said.`, "Approve anyway"))) return;
  try {
    const done = await ctx.call("article.approve", { id: d.piece.id });
    await decided(ctx, done?.said || "Approved.");
  } catch (error) {
    say(why(error), "bad");
  }
}

export async function edit(ctx) {
  if (canEditSent(ctx.state.detail)) return editSent(ctx);
  const d = waiting(ctx);
  if (!d) return undefined;
  const saved = await editArticle(ctx, d);
  if (saved) {
    await ctx.refresh();
    const after = ctx.state.detail;
    say(`Saved as version ${after?.revision?.n ?? ""}. The checks ran again: ${checkSummary(after?.checks ?? [])}`, "ok");
  }
  return undefined;
}

/** Edit an article already sent, and send the edit where the article went. The app's own
 * answer says whether the site was changed; it stays on the article until it is closed. */
export async function editSent(ctx) {
  const d = ctx.state.detail;
  if (!canEditSent(d)) return;
  const before = d.attempts;
  const done = await editArticle(ctx, d, {
    ...editSentWords(d.site),
    save: (title, body) => ctx.call("article.edit_sent", { id: d.piece.id, title, body }),
  });
  if (!done) return;
  await ctx.refresh();
  const after = ctx.state.detail;
  const said = done.said || "Your edit is kept here as a new version.";
  const tone = editSentTone(before, after?.attempts);
  ctx.state.sentSaid = { piece: d.piece.id, said, tone };
  ctx.render();
  say(said, tone);
}

export async function sendBack(ctx) {
  const d = waiting(ctx);
  if (!d) return;
  const note = h("textarea", { rows: 5, placeholder: "What should change, in your own words." });
  const result = await dialog({
    title: "Send it back", intro: "Articles rewrites it from your note, checks it again, and brings it back to you. Your note is a lesson for later articles of this site.",
    body: field("Your note", note),
    actions: [{ label: "Send back", kind: "primary", run: async () => {
      if (!note.value.trim()) return "Say what to change when you send an article back. Nothing was changed.";
      await ctx.call("article.send_back", { id: d.piece.id, note: note.value });
    } }],
  });
  if (result) await decided(ctx, "Sent back with your note.");
}

export async function decline(ctx) {
  const d = waiting(ctx);
  if (!d) return;
  const reasons = ctx.data().decline_reasons ?? [];
  const pick = h("select", {}, reasons.map((r) => h("option", { value: r }, r)));
  const note = h("textarea", { rows: 3, placeholder: "A few words on why" });
  const result = await dialog({
    title: "Decline it", intro: "Nothing is published. Your reason is a lesson for later articles of this site.",
    body: h("div", { class: "stack" }, reasons.length ? field("Reason", pick) : null, field("Note", note)),
    actions: [{ label: "Decline", kind: "danger", run: async () => {
      const reason = declineReason(reasons.length ? pick.value : "", note.value);
      if (!reason) return "Give a reason when you decline an article. Nothing was changed.";
      await ctx.call("article.decline", { id: d.piece.id, reason });
    } }],
  });
  if (result) await decided(ctx, "Declined. Your reason is a lesson for later articles.");
}

export async function drop(ctx) {
  const piece = ctx.state.detail?.piece;
  if (!piece) return;
  if (!(await askSafely({ dialog, document }, DROP_ARTICLE.title, DROP_ARTICLE.intro, DROP_ARTICLE.yes))) return;
  await run(ctx, "piece.drop", { id: piece.id }, "Dropped.");
}

export async function retry(ctx) {
  const piece = ctx.state.detail?.piece;
  if (piece) await run(ctx, "piece.retry", { id: piece.id }, "Trying that step again.");
}

export async function writeAnyway(ctx) {
  const piece = ctx.state.detail?.piece;
  if (piece) await run(ctx, "piece.write_anyway", { id: piece.id }, "Writing it anyway: research comes next.");
}

export async function answer(ctx, check, value) {
  try {
    await ctx.call("check.answer", { id: check.id, answer: value });
    say(value === "fixed" ? "Kept. Edit the text to fix it; the checks run again when you save." : "Your answer is kept with the article.", "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

/** The buttons for the article's state. */
export function actions(ctx, d) {
  const { piece } = d;
  const btn = (label, fn, kind = "") => h("button", { type: "button", class: `btn ${kind}`.trim(), onclick: () => fn(ctx) }, label);
  const out = [];
  if (piece.state === "ready" && d.revision) {
    out.push(btn("Approve", approve, "primary"), btn("Edit", edit), btn("Send back", sendBack), btn("Decline", decline, "danger"));
  }
  if (piece.state === "held_coverage") out.push(btn("Write anyway", writeAnyway, "primary"));
  if (["stalled", "stopped_research"].includes(piece.state)) out.push(btn("Retry", retry, "primary"));
  if (canEditSent(d)) out.push(btn(editSentWords(d.site).button, editSent));
  if (["approved", "publishing", "unsure", "sent"].includes(piece.state)) {
    out.push(h("button", { type: "button", class: "btn primary", onclick: () => ctx.go("publish", { piece: piece.id }) }, "Go to Publish"));
  }
  if (!["publishing", "unsure", "sent", "published", "declined", "dropped"].includes(piece.state)) out.push(btn("Drop", drop, "danger"));
  return out.length ? h("div", { class: "actions" }, out) : null;
}
