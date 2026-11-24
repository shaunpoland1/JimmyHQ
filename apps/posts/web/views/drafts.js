// Drafts: each piece with its draft beside its source. A number or a quotation in the
// draft lights up the source sentence it rests on; what the checks could not find is
// marked, with its question and Yes / No / Fixed. Approve, Edit, Send back, Decline.

import { STANDING_WORDS, checkSummary, citedSentences, claimsBySentence, raised, segments } from "../lib/evidence.js";
import { declineReason } from "../lib/forms.js";
import { approvalQuestion } from "../lib/approve.js";
import { keyHint } from "../lib/keys.js";
import { draftsToDecide, headline, inTheWorks, pageName } from "../lib/lists.js";
import { formatSlot } from "../lib/time.js";
import { askChips, noticeButtons, noticeChip } from "../lib/trouble.js";
import { cameFrom, whoWorked, workLines } from "../lib/work.js";
import { ask, dialog, field, h, say, why } from "../_ui.js";

const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** The drafts the keys move through, in the order the list shows them. */
export function draftList(ctx) {
  return draftsToDecide(ctx.data().posts);
}

export function render(ctx) {
  const data = ctx.data();
  const drafts = draftList(ctx);
  const working = inTheWorks(data.pieces, data.posts);
  const state = ctx.state;
  const letters = data.letter_keys !== false;
  const hint = keyHint(letters);

  const list = h("ol", { class: "picklist", "aria-label": "Drafts to decide" }, drafts.map((post, i) => h("li", {},
    h("button", {
      type: "button", class: `pick${post.piece_id === state.openPiece ? " current" : ""}${i === state.selected ? " cursor" : ""}`,
      "aria-current": post.piece_id === state.openPiece ? "true" : undefined,
      onclick: () => { state.selected = i; ctx.open(post.piece_id); },
    },
    h("span", { class: "small muted" }, pageName(data.pages, post.page_id)),
    h("span", { class: "strong" }, headline(post)),
    h("span", { class: "chips" },
      ...askChips(post.asks).map((a) => h("span", { class: `chip ${a.kind}` }, a.text)),
      post.warnings ? h("span", { class: "chip warn" }, plural(post.warnings, "warning")) : null,
      !post.questions && !post.warnings ? h("span", { class: "chip ok" }, "Every check passed") : null)))));

  const side = h("div", { class: "side" },
    ...(data.notices ?? []).map((n) => h("div", { class: "banner", role: "note" },
      h("p", { class: "small" }, `${pageName(data.pages, n.page_id)}: ${n.why}`),
      h("div", { class: "row wrap" }, noticeButtons(n).map((b) => h("button", { type: "button", class: `btn small ${b.kind}`.trim(), onclick: () => choose(ctx, n, b.action) }, b.label))))),
    h("h2", {}, drafts.length ? `To decide (${drafts.length})` : "To decide"),
    drafts.length ? list : h("p", { class: "muted" }, "No drafts are waiting for you. New ones appear here once Posts has drafted and checked them."),
    working.length ? h("h2", {}, "Being worked on") : null,
    working.length ? h("ul", { class: "picklist" }, working.map((piece) => h("li", {},
      h("button", { type: "button", class: `pick${piece.id === state.openPiece ? " current" : ""}`, onclick: () => ctx.open(piece.id) },
        h("span", { class: "small muted" }, pageName(data.pages, piece.page_id)),
        h("span", { class: "strong" }, piece.source_title || piece.topic || piece.source_url || `Piece ${piece.id}`),
        piece.origin ? h("span", { class: "small muted" }, piece.origin) : null,
        h("span", { class: `chip ${["stalled", "stopped_source", "held_overlap"].includes(piece.state) ? "warn" : ""}` }, piece.words),
        noticeChip(piece.notice) ? h("span", { class: "chip warn" }, noticeChip(piece.notice).text) : null)))) : null,
    h("p", { class: "keys small muted", "aria-label": "Keys" }, hint.keys),
    h("p", { class: "small muted" }, hint.note));

  const main = h("div", { class: "detail", id: "detail" },
    state.detail && state.detail.piece.id === state.openPiece ? detail(ctx, state.detail)
      : h("p", { class: "muted pad" }, drafts.length ? (letters ? "Choose a draft, or press j." : "Choose a draft.") : "Nothing open."));
  return h("div", { class: "split" }, side, main);
}

// --- one piece ---------------------------------------------------------------------------

function detail(ctx, d) {
  const { piece, post } = d;
  const data = ctx.data();
  const checks = d.checks ?? [];
  const zone = ctx.zoneOfPage(piece.page_id);
  const title = post?.title || piece.source_title || piece.topic || headline(post ?? {}) || `Piece ${piece.id}`;
  const head = h("header", { class: "detail-head" },
    h("h2", {}, title),
    h("p", { class: "small" }, `${pageName(data.pages, piece.page_id)} · ${cameFrom(d.turns)} · ${post ? data.posts.find((p) => p.id === post.id)?.words ?? post.state : piece.words}`),
    piece.origin ? h("p", { class: "small" }, `Came ${piece.origin}.`) : null,
    d.worked ? h("p", { class: "small muted" }, d.worked) : null,
    h("p", { class: "small muted" }, whoWorked(d.turns)),
    piece.error ? h("p", { class: "problem" }, piece.error) : null,
    post?.note ? h("p", { class: "note" }, post.note) : null,
    h("button", { type: "button", class: "link", "aria-expanded": String(ctx.state.showWork), onclick: () => { ctx.state.showWork = !ctx.state.showWork; ctx.render(); } },
      ctx.state.showWork ? "Hide the work" : "Show the work"),
    ctx.state.showWork ? work(d.turns, zone, data.now_ms) : null);

  const draftSide = post ? draftColumn(ctx, d, checks) : h("section", { class: "col" }, h("h3", {}, "Draft"), h("p", { class: "muted" }, `No draft yet: ${piece.words}.`));
  const sourceSide = sourceColumn(ctx, d, checks);
  return h("article", { class: "piece" }, head, h("div", { class: "pair" }, draftSide, sourceSide), actions(ctx, d));
}

function work(turns, zone, nowMs) {
  return h("ol", { class: "work" }, workLines(turns).map((line) => h("li", {},
    h("span", { class: "small muted" }, formatSlot(line.when, zone, nowMs)), " ",
    h("span", { class: "strong" }, `${line.step}`), h("span", { class: "muted" }, ` · ${line.who}: `),
    line.gloss)));
}

function draftColumn(ctx, d, checks) {
  const { post, piece } = d;
  const body = h("p", { class: "body" }, segments(post.body, checks).map((seg) => {
    if (seg.kind === "text") return seg.text;
    const where = seg.sentences.length ? `, sentence ${seg.sentences.join(", ")}` : "";
    return h("button", {
      type: "button", class: `ev ${seg.standing}`,
      "aria-label": `${seg.text}: ${STANDING_WORDS[seg.standing]}${where}`,
      title: `${STANDING_WORDS[seg.standing]}${where}`,
      onclick: () => light(ctx, seg),
    }, seg.text);
  }));
  const claims = (post.claims ?? []).filter((c) => c && c.text);
  const results = raised(checks);
  const passed = checks.filter((c) => c.level === "ok");
  return h("section", { class: "col", "aria-labelledby": "draft-h" },
    h("h3", { id: "draft-h" }, `Draft${post.revision > 1 ? `, version ${post.revision}` : ""}`, piece.sponsored ? h("span", { class: "chip" }, "Sponsored") : null),
    post.title ? h("p", { class: "strong" }, post.title) : null,
    body,
    h("p", { class: "small muted" }, "Press a marked number or quotation to see the sentence it rests on."),
    claims.length ? h("div", { class: "claims" }, h("h4", {}, "What it rests on"), h("ul", {}, claims.map((claim) => h("li", {},
      claim.text, " ",
      claim.sentences?.length ? claim.sentences.map((n) => h("button", { type: "button", class: "chip link", onclick: () => light(ctx, { sentences: [n] }) }, `sentence ${n}`))
        : h("span", { class: "chip ask" }, "cites no sentence"))))) : null,
    h("div", { class: "checks", id: "checks" },
      h("h4", {}, checkSummary(checks)),
      results.map((result) => checkRow(ctx, result)),
      passed.length ? h("details", {}, h("summary", {}, `${plural(passed.length, "check")} passed`),
        h("ul", { class: "small" }, passed.map((c) => h("li", {}, c.detail)))) : null));
}

function checkRow(ctx, result) {
  const sentences = (result.evidence ?? []).filter((e) => Number.isInteger(e));
  const answers = result.level === "ask" ? h("span", { class: "answers", role: "group", "aria-label": "Answer" },
    [["yes", "Yes"], ["no", "No"], ["fixed", "Fixed"]].map(([value, words]) => h("button", {
      type: "button", class: `btn small${result.answer === value ? " primary" : ""}`, "aria-pressed": String(result.answer === value),
      onclick: async () => {
        try {
          await ctx.call("check.answer", { id: result.id, answer: value });
          say(value === "fixed" ? "Kept. Edit the text to fix it; the checks run again then." : "Your answer is kept with the draft.", "ok");
          await ctx.refresh();
        } catch (error) {
          say(why(error), "bad");
        }
      },
    }, words))) : null;
  return h("div", { class: `check ${result.level}`, id: `check-${result.id}` },
    h("span", { class: "mark", "aria-hidden": "true" }, result.level === "ask" ? "?" : "!"),
    h("span", { class: "words" }, result.level === "ask" ? "Question: " : "Warning: ", result.detail,
      result.answer ? h("span", { class: "muted" }, ` You said ${result.answer}.`) : null),
    sentences.length ? h("button", { type: "button", class: "chip link", onclick: () => light(ctx, { sentences }) }, `sentence ${sentences.join(", ")}`) : null,
    answers);
}

function sourceColumn(ctx, d, checks) {
  const { piece, post } = d;
  const pics = d.pictures ?? [];
  const notes = piece.notes ? h("div", { class: "notes" }, h("h4", {}, "Your notes"), h("p", { class: "body" }, piece.notes)) : null;
  if (pics.length) {
    return h("section", { class: "col", "aria-labelledby": "source-h" }, h("h3", { id: "source-h" }, "Pictures"),
      h("ol", { class: "pictures" }, pics.map((pic, i) => {
        const img = h("img", { alt: pic.note || `Picture ${i + 1}` });
        if (pic.ref) ctx.thumbnail(pic.ref).then((url) => { if (url) img.src = url; });
        const a = pic.analysis;
        return h("li", {}, pic.ref ? img : h("p", { class: "muted small" }, "Deleted after its post went out."),
          h("p", { class: "small strong" }, `Picture ${i + 1}${pic.note ? `: ${pic.note}` : ""}`),
          a ? h("p", { class: "small" }, a.what, a.text_seen ? ` Text in it: “${a.text_seen}”.` : "") : h("p", { class: "small muted" }, "Not looked at yet."),
          pic.looks_like ? h("p", { class: "small warn-text" }, pic.looks_like) : null);
      })), notes);
  }
  const sentences = piece.sentences ?? [];
  if (!sentences.length) {
    return h("section", { class: "col", "aria-labelledby": "source-h" }, h("h3", { id: "source-h" }, "Source"),
      h("p", { class: "muted" }, piece.source_url ? "The article has not been read yet." : "There is no article: this piece rests only on your topic and notes."),
      piece.topic ? h("p", {}, h("span", { class: "strong" }, "Topic: "), piece.topic) : null, notes);
  }
  const lit = new Set(ctx.state.lit);
  const cited = citedSentences(post?.claims, checks);
  const claimed = claimsBySentence(post?.claims);
  return h("section", { class: "col", "aria-labelledby": "source-h" },
    h("h3", { id: "source-h" }, "Source"),
    piece.source_title ? h("p", { class: "strong" }, piece.source_title) : null,
    piece.source_url ? h("p", { class: "small muted address" }, piece.source_url) : null,
    h("ol", { class: "sentences", id: "sentences" }, sentences.map((text, i) => {
      const n = i + 1;
      return h("li", {
        id: `s-${n}`, value: n, class: `${lit.has(n) ? "lit" : ""}${cited.has(n) ? " cited" : ""}`.trim() || undefined,
        title: claimed.has(n) ? `Cited for: ${claimed.get(n).join(" / ")}` : undefined,
      }, text);
    })), notes);
}

/** Light the sentences a number or quotation rests on; with none, show its question. */
function light(ctx, seg) {
  ctx.state.lit = seg.sentences ?? [];
  for (const el of document.querySelectorAll("#sentences li.lit")) el.classList.remove("lit");
  let first = null;
  for (const n of ctx.state.lit) {
    const el = document.getElementById(`s-${n}`);
    if (el) {
      el.classList.add("lit");
      first ??= el;
    }
  }
  if (first) {
    first.scrollIntoView({ block: "center", behavior: "smooth" });
    return;
  }
  const row = seg.check ? document.getElementById(`check-${seg.check.id}`) : null;
  if (row) {
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    row.classList.add("flash");
    setTimeout(() => row.classList.remove("flash"), 1600);
    row.querySelector("button")?.focus();
  }
  say(seg.check ? seg.check.detail : "This version has not been checked yet.", seg.standing === "not_found" ? "warn" : "");
}

// --- deciding ----------------------------------------------------------------------------

function actions(ctx, d) {
  const { piece, post } = d;
  const buttons = [];
  if (post && post.state === "draft" && piece.state === "ready") {
    buttons.push(h("button", { type: "button", class: "btn primary", onclick: () => approve(ctx) }, "Approve"),
      h("button", { type: "button", class: "btn", onclick: () => edit(ctx) }, "Edit"),
      h("button", { type: "button", class: "btn", onclick: () => sendBack(ctx) }, "Send back"),
      h("button", { type: "button", class: "btn danger", onclick: () => decline(ctx) }, "Decline"));
  } else if (post && ["queued", "booked"].includes(post.state)) {
    buttons.push(h("button", { type: "button", class: "btn", onclick: () => edit(ctx) }, "Edit"),
      h("button", { type: "button", class: "btn", onclick: () => ctx.go("queue") }, "See it in the queue"));
  }
  const notice = ctx.data().pieces.find((p) => p.id === piece.id)?.notice;
  if (notice) {
    // Agent work waits on the owner: the same choices as its card on the Bench.
    for (const b of noticeButtons(notice)) {
      buttons.push(h("button", { type: "button", class: `btn ${b.kind}`.trim(), onclick: () => choose(ctx, notice, b.action) }, b.label));
    }
  } else if (["stalled", "stopped_source"].includes(piece.state)) {
    buttons.push(h("button", { type: "button", class: "btn primary", onclick: () => pieceAction(ctx, "piece.retry", "Trying again.") }, "Retry"));
  }
  if (piece.state === "held_overlap") {
    buttons.push(h("button", { type: "button", class: "btn primary", onclick: () => pieceAction(ctx, "piece.draft_anyway", "Drafting it anyway.") }, "Draft anyway"));
  }
  if (!notice && !["closed", "dropped", "ready"].includes(piece.state)) {
    buttons.push(h("button", { type: "button", class: "btn danger", onclick: () => drop(ctx) }, "Drop"));
  }
  return buttons.length ? h("div", { class: "actions" }, buttons) : null;
}

function current(ctx) {
  const d = ctx.state.detail;
  return d && d.post && d.post.state === "draft" && d.piece.state === "ready" ? d : null;
}

async function decided(ctx, words) {
  say(words, "ok");
  const before = ctx.state.selected;
  await ctx.refresh();
  const next = draftList(ctx);
  if (next.length) {
    ctx.state.selected = Math.min(Math.max(before, 0), next.length - 1);
    await ctx.open(next[ctx.state.selected].piece_id);
  } else {
    ctx.close();
  }
}

/**
 * Approve the open draft. With `byKey` (the "a" key) it always asks first, naming the
 * time and whether approving books it: one stray key press must not book a post.
 */
export async function approve(ctx, { byKey = false } = {}) {
  const d = current(ctx);
  if (!d) return;
  const open = (d.checks ?? []).filter((c) => c.level === "ask" && !c.answer).length;
  if (byKey) {
    let preview;
    try {
      preview = await ctx.call("post.approval", { id: d.post.id });
    } catch (error) {
      say(why(error), "bad");
      return;
    }
    const when = preview.slot_ms ? formatSlot(preview.slot_ms, ctx.zoneOfPage(d.piece.page_id), ctx.data().now_ms) : "";
    const q = approvalQuestion({ open, preview, when });
    if (!(await ask(q.title, q.intro, q.yes))) return;
  } else if (open && !(await ask("Approve with questions open?", `${plural(open, "question")} on this draft ${open === 1 ? "is" : "are"} not answered. You can approve anyway; the post keeps the warning.`, "Approve anyway"))) return;
  try {
    const post = await ctx.call("post.approve", { id: d.post.id });
    const zone = ctx.zoneOfPage(d.piece.page_id);
    const when = post.slot_ms ? ` for ${formatSlot(post.slot_ms, zone, ctx.data().now_ms)}` : "";
    await decided(ctx, post.state === "booked" ? `Approved and booked${when}.` : post.note ? `Approved. ${post.note}` : `Approved and queued${when}.`);
  } catch (error) {
    say(why(error), "bad");
  }
}

export async function edit(ctx) {
  const d = ctx.state.detail;
  if (!d?.post || !["draft", "queued", "booked"].includes(d.post.state)) return;
  const title = h("input", { type: "text", value: d.post.title || "", maxlength: 200 });
  const body = h("textarea", { rows: 12, value: d.post.body });
  const result = await dialog({
    title: "Edit the post", intro: "The checks run again on your text when you save.",
    body: h("div", { class: "stack" }, field("Title", title), field("Text", body)),
    actions: [{ label: "Save", kind: "primary", run: async () => {
      if (!body.value.trim()) return "A post needs some text. Nothing was changed.";
      await ctx.call("post.edit", { id: d.post.id, title: title.value, body: body.value });
    } }],
  });
  if (result) {
    await ctx.refresh();
    const checks = ctx.state.detail?.checks ?? [];
    say(`Saved. The checks ran again: ${checkSummary(checks)}`, "ok");
  }
}

export async function sendBack(ctx) {
  const d = current(ctx);
  if (!d) return;
  const note = h("textarea", { rows: 5, placeholder: "What should change? Put a phrase in quotation marks to have it flagged on later drafts." });
  const result = await dialog({
    title: "Send it back", intro: "Posts revises the draft with your note, checks it again, and brings it back.",
    body: field("Your note", note),
    actions: [{ label: "Send back", kind: "primary", run: async () => {
      if (!note.value.trim()) return "Say what to change when you send a draft back. Nothing was changed.";
      await ctx.call("post.send_back", { id: d.post.id, note: note.value });
    } }],
  });
  if (result) await decided(ctx, "Sent back with your note.");
}

export async function decline(ctx) {
  const d = current(ctx);
  if (!d) return;
  const reasons = ctx.data().decline_reasons;
  const pick = h("select", {}, reasons.map((r) => h("option", { value: r }, r)));
  const note = h("textarea", { rows: 3, placeholder: "A few words on why (optional)" });
  const result = await dialog({
    title: "Decline it", intro: "Your reason teaches later drafts for this brand.",
    body: h("div", { class: "stack" }, field("Reason", pick), field("Note", note)),
    actions: [{ label: "Decline", kind: "danger", run: async () => {
      const reason = declineReason(reasons.length ? pick.value : "", note.value);
      if (!reason) return "Give a reason when you decline a draft. Nothing was changed.";
      await ctx.call("post.decline", { id: d.post.id, reason });
    } }],
  });
  if (result) await decided(ctx, "Declined.");
}

const CHOSEN = { wait: "Waiting for the agent again.", finish_plain: "Finishing it without agents.", drop: "Dropped." };

async function choose(ctx, notice, action) {
  if (action === "drop" && !(await ask("Drop this?", notice.piece_id ? "Nothing more is spent on it, and any draft of it is declined. It stays under Dropped." : "Posts stops asking for ideas for this brand until tomorrow.", "Drop it", "danger"))) return;
  try {
    await ctx.call("work.choose", { id: notice.work_id, action });
    say(CHOSEN[action] ?? "Done.", "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function pieceAction(ctx, call, words) {
  try {
    await ctx.call(call, { id: ctx.state.detail.piece.id });
    say(words, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function drop(ctx) {
  const piece = ctx.state.detail?.piece;
  if (!piece) return;
  if (!(await ask("Drop this piece?", "Nothing more is spent on it, and any draft of it is declined. It stays under Dropped.", "Drop it", "danger"))) return;
  try {
    await ctx.call("piece.drop", { id: piece.id });
    await decided(ctx, "Dropped.");
  } catch (error) {
    say(why(error), "bad");
  }
}
