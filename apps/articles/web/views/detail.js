// One article: the article beside its sources, the research, the versions and the review,
// and under "Show the work" one line for each step. Pressing a number, a quotation, a
// citation or a cited sentence lights the source sentences it rests on, across sources;
// what the checks could not find points at its question, with Yes / No / Fixed.

import {
  STANDING_WORDS, byId, checkSummary, citedEverywhere, describeLight, lightFor, raised, segments,
  sentenceChecks, sentenceStanding, target,
} from "../lib/evidence.js";
import { build, parse } from "../lib/markup.js";
import { formatWhen } from "../lib/time.js";
import { h, say } from "../_ui.js";
import { actions, answer } from "./decide.js";
import { headerSection } from "./header.js";

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const TABS = [["article", "Article and sources"], ["research", "Research"], ["versions", "Versions and review"]];

export const STEP_WORDS = {
  started: "Started", coverage_check: "Earlier articles", held_coverage: "Held", searching: "Search",
  reading: "Reading", stopped_research: "Too few sources", summarising: "Research summary",
  outlining: "Outline", drafting: "Writing", reviewing: "Review", repairing: "Repair",
  rechecking: "Repair checked", revising: "Rewrite from your note", ready: "Your decision",
  approved: "Approved", publishing: "Publishing", unsure: "No answer", sent: "Sent", stalled: "Stuck",
  header: "Header image",
};

const KIND_WORDS = { draft: "first draft", repair: "repair pass", edit: "your edit", owner_note: "rewritten from your note" };

export function who(turn) {
  if (turn.role === "owner") return "You";
  if (turn.role === "code") return "The code";
  const { local } = turn;
  const where = local === true ? ", on your own computer" : local === false ? ", a hosted model" : "";
  return `Model role “${turn.role}”${turn.model ? ` (${turn.model}${where})` : where ? ` (${where.slice(2)})` : ""}`;
}

export function detail(ctx, d) {
  const state = ctx.state;
  const { piece, revision } = d;
  const zone = ctx.zoneOfSite(piece.site_id);
  const title = revision?.title || piece.topic;
  const tabs = h("div", { class: "segmented", role: "tablist", "aria-label": "What to show" }, TABS.map(([id, words]) => h("button", {
    type: "button", role: "tab", "aria-selected": String(state.tab === id), class: state.tab === id ? "on" : "",
    onclick: () => { state.tab = id; ctx.render(); },
  }, words)));
  const head = h("header", { class: "detail-head" },
    h("h2", {}, title),
    h("p", { class: "small" }, [d.site?.name ?? "A site you removed", piece.words,
      revision ? `${revision.words} words from ${plural(d.sources.length, "source")}` : null].filter(Boolean).join(" · ")),
    piece.angle ? h("p", { class: "small" }, h("span", { class: "strong" }, "Angle: "), piece.angle) : null,
    h("p", { class: "small muted" }, `Ways on: ${piece.ways_out}.`),
    piece.error ? h("p", { class: "problem" }, piece.error) : null,
    piece.owner_note ? h("p", { class: "note" }, `Your note: ${piece.owner_note}`) : null,
    d.close?.close ? h("p", { class: "note" }, d.close.said) : null,
    state.sentSaid?.piece === piece.id ? h("p", { class: state.sentSaid.tone === "ok" ? "small" : "note" }, `Your edit: ${state.sentSaid.said}`) : null,
    h("button", { type: "button", class: "link", "aria-expanded": String(state.showWork), onclick: () => { state.showWork = !state.showWork; ctx.render(); } },
      state.showWork ? "Hide the work" : "Show the work"),
    state.showWork ? work(d, zone) : null);
  const body = state.tab === "research" ? research(ctx, d) : state.tab === "versions" ? versions(ctx, d, zone) : articleTab(ctx, d);
  return h("article", { class: "piece" }, head, tabs, body, headerSection(ctx, d), actions(ctx, d));
}

function work(d, zone) {
  if (!d.turns.length) return h("p", { class: "small muted" }, "Nothing has been done yet.");
  return h("ol", { class: "work" }, d.turns.map((turn) => h("li", {},
    h("span", { class: "small muted" }, formatWhen(turn.created, zone, d.now_ms)), " ",
    h("span", { class: "strong" }, STEP_WORDS[turn.step] ?? turn.step), h("span", { class: "muted" }, ` · ${who(turn)}: `),
    turn.gloss)));
}

// --- lighting ------------------------------------------------------------------------------

/** Light what `what` rests on: a mark, a sentence, a check, or a list of [s, n] pairs. */
export function light(ctx, what) {
  const d = ctx.state.detail;
  const checks = byId(d?.checks);
  const found = Array.isArray(what) ? { light: lightFor(what), check: null } : target(what, checks);
  ctx.state.lit = found.light;
  if (ctx.state.tab !== "article") ctx.state.tab = "article";
  for (const n of found.light.keys()) ctx.state.openSources.add(n);
  ctx.render();
  const first = firstLit(found.light);
  if (first) {
    first.scrollIntoView({ block: "center", behavior: "smooth" });
    say(`Shown in the sources: ${describeLight(found.light)}.`);
    return;
  }
  const check = found.check;
  const row = check ? document.getElementById(`check-${check.id}`) : null;
  if (row) {
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    row.classList.add("flash");
    setTimeout(() => row.classList.remove("flash"), 1600);
    row.querySelector("button")?.focus();
  }
  say(check ? check.detail : "Nothing in the sources is tied to this.", check?.level === "ask" ? "warn" : "");
}

function firstLit(lit) {
  for (const [n, sentences] of lit) {
    const el = sentences.length ? document.getElementById(`s-${n}-${sentences[0]}`) : document.getElementById(`src-${n}`);
    if (el) return el;
  }
  return null;
}

// --- the article and its sources --------------------------------------------------------------

function articleTab(ctx, d) {
  if (!d.revision) {
    return h("div", { class: "pair" }, h("section", { class: "col" }, h("h3", {}, "Article"),
      h("p", { class: "muted" }, `No text yet: it is ${d.piece.words}.`),
      d.piece.notes ? h("div", { class: "notes" }, h("h4", {}, "Your notes"), h("p", { class: "body" }, d.piece.notes)) : null),
    sourcesColumn(ctx, d));
  }
  return h("div", { class: "pair" }, articleColumn(ctx, d), sourcesColumn(ctx, d));
}

function articleColumn(ctx, d) {
  const state = ctx.state;
  const checks = byId(d.checks);
  const reading = h("div", { class: "segmented small-seg", role: "group", "aria-label": "How to read it" },
    [["evidence", "With its evidence"], ["reader", "As readers see it"]].map(([id, words]) => h("button", {
      type: "button", class: state.reading === id ? "on" : "", "aria-pressed": String(state.reading === id),
      onclick: () => { state.reading = id; ctx.render(); },
    }, words)));
  const text = state.reading === "reader"
    ? h("div", { class: "article-text" }, h("h3", { class: "article-title" }, d.revision.title),
      build(parse(d.revision.body, new Set(d.sources.map((s) => s.n))).nodes, h, {
        onCite: (pairs) => light(ctx, pairs), onSource: (n) => light(ctx, [`S${n}`]),
      }))
    : evidenceText(ctx, d, checks);
  return h("section", { class: "col", "aria-labelledby": "article-h" },
    h("h3", { id: "article-h" }, `Article, version ${d.revision.n}`), reading, text,
    h("p", { class: "small muted" }, "Press a number, a quotation or a sentence to see the source sentence it rests on."),
    checksBlock(ctx, d));
}

function evidenceText(ctx, d, checks) {
  const out = [];
  let list = null;
  for (const block of d.blocks) {
    const sentences = block.sentences.map((s) => [sentence(ctx, s, checks), " "]);
    if (block.tag === "li") {
      if (!list) {
        list = h("ul", { class: "article-list" });
        out.push(list);
      }
      list.appendChild(h("li", {}, sentences));
      continue;
    }
    list = null;
    const tag = { h1: "h3", h2: "h4", h3: "h5", blockquote: "blockquote" }[block.tag] ?? "p";
    out.push(h(tag, { class: block.tag === "h1" ? "article-title" : "" }, sentences));
  }
  return h("div", { class: "article-text evidence" }, out);
}

function sentence(ctx, s, checks) {
  const standing = sentenceStanding(s, checks);
  const raisedHere = sentenceChecks(s, checks).filter((c) => !(s.marks ?? []).some((m) => m.check === c.id));
  const runs = segments(s, checks).map((seg) => {
    if (seg.kind === "text") return seg.text;
    const where = seg.light.size ? `, ${describeLight(seg.light)}` : "";
    return h("button", {
      type: "button", class: `ev ${seg.standing}`, title: `${STANDING_WORDS[seg.standing]}${where}`,
      "aria-label": `${seg.text}: ${STANDING_WORDS[seg.standing]}${where}`,
      onclick: (event) => { event.stopPropagation(); light(ctx, seg); },
    }, seg.text);
  });
  const cites = (s.cites ?? []).length ? h("button", {
    type: "button", class: "chip cite", "aria-label": `Show what this sentence cites: ${describeLight(lightFor(s.cites))}`,
    onclick: (event) => { event.stopPropagation(); light(ctx, s); },
  }, describeLight(lightFor(s.cites))) : null;
  const asks = raisedHere.length ? h("button", {
    type: "button", class: `chip ${raisedHere.some((c) => c.level === "ask" && !c.answer) ? "ask" : "warn"}`,
    "aria-label": `${plural(raisedHere.length, "question or warning", "questions or warnings")} about this sentence`,
    onclick: (event) => { event.stopPropagation(); light(ctx, raisedHere[0]); },
  }, raisedHere.length === 1 ? "?" : `? ${raisedHere.length}`) : null;
  return h("span", { class: `sent ${standing}`, id: `a-${s.i}`, onclick: () => light(ctx, s) }, runs, cites ? " " : null, cites, asks ? " " : null, asks);
}

function checksBlock(ctx, d) {
  const passed = d.checks.filter((c) => c.level === "ok");
  return h("div", { class: "checks", id: "checks" },
    h("h4", {}, checkSummary(d.checks)),
    raised(d.checks).map((c) => checkRow(ctx, d, c)),
    passed.length ? h("details", {}, h("summary", {}, `${plural(passed.length, "check")} passed`),
      h("ul", { class: "small passed" }, passed.map((c) => h("li", {}, c.detail, " ", evidenceChips(ctx, c))))) : null);
}

function evidenceChips(ctx, check) {
  const lit = lightFor(check.evidence ?? []);
  if (!lit.size) return null;
  return h("button", { type: "button", class: "chip cite", onclick: () => light(ctx, check) }, describeLight(lit));
}

function checkRow(ctx, d, c) {
  const canAnswer = c.level === "ask" && d.piece.state === "ready";
  const answers = canAnswer ? h("span", { class: "answers", role: "group", "aria-label": "Answer" },
    [["yes", "Yes"], ["no", "No"], ["fixed", "Fixed"]].map(([value, words]) => h("button", {
      type: "button", class: `btn small${c.answer === value ? " primary" : ""}`, "aria-pressed": String(c.answer === value),
      onclick: () => answer(ctx, c, value),
    }, words))) : null;
  return h("div", { class: `check ${c.level}${c.answer ? " answered" : ""}`, id: `check-${c.id}` },
    h("span", { class: "mark", "aria-hidden": "true" }, c.level === "ask" ? "?" : "!"),
    h("span", { class: "words" }, c.level === "ask" ? "Question: " : "Warning: ", c.detail,
      c.answer ? h("span", { class: "muted" }, ` You said ${c.answer}.`) : null),
    evidenceChips(ctx, c), answers);
}

function sourcesColumn(ctx, d) {
  const state = ctx.state;
  const lit = state.lit ?? new Map();
  const cited = citedEverywhere(d.blocks);
  if (!d.sources.length) {
    return h("section", { class: "col" }, h("h3", {}, "Sources"), h("p", { class: "muted" }, "No sources have been kept yet."));
  }
  return h("section", { class: "col sources", "aria-labelledby": "sources-h" },
    h("h3", { id: "sources-h" }, `Sources (${d.sources.length})`),
    d.sources.map((source) => {
      const n = source.n;
      const sentences = state.sentences.get(n);
      const litHere = new Set(lit.get(n) ?? []);
      const citedHere = new Set(cited.get(n) ?? []);
      const box = h("details", { class: `source${lit.has(n) ? " lit-source" : ""}`, id: `src-${n}`, open: state.openSources.has(n) },
        h("summary", {}, h("span", { class: "chip" }, `S${n}`), " ", h("span", { class: "strong" }, source.title || source.host),
          " ", h("span", { class: "small muted" }, source.host), source.weak ? h("span", { class: "chip warn" }, "weak") : null),
        h("p", { class: "small muted address" }, source.final_url || source.url),
        sentences === undefined ? h("p", { class: "small muted" }, "Loading its sentences…")
          : h("ol", { class: "sentences" }, sentences.list.map((text, i) => {
            const k = i + 1;
            return h("li", { id: `s-${n}-${k}`, value: k, class: `${litHere.has(k) ? "lit" : ""}${citedHere.has(k) ? " cited" : ""}`.trim() || undefined }, text);
          })),
        sentences?.cut ? h("p", { class: "small note" }, `Only the first ${sentences.list.length} of its ${source.sentences} sentences fit on this page.`) : null);
      box.addEventListener("toggle", () => { if (box.open) state.openSources.add(n); else state.openSources.delete(n); });
      return box;
    }));
}

// --- research ------------------------------------------------------------------------------

function research(ctx, d) {
  const parts = [];
  if (d.close) parts.push(h("section", {}, h("h4", {}, "Earlier articles"), h("p", {}, d.close.said), d.close.note ? h("p", { class: "small muted" }, d.close.note) : null));
  if (d.phrasings.length) parts.push(h("section", {}, h("h4", {}, "Searched for"), h("ul", { class: "bullets" }, d.phrasings.map((p) => h("li", {}, p)))));
  parts.push(h("section", {}, h("h4", {}, `Sources kept (${d.sources.length})`),
    d.sources.length ? h("ul", { class: "rows" }, d.sources.map((s) => h("li", { class: "kept" },
      h("p", {}, h("span", { class: "chip" }, `S${s.n}`), " ", h("span", { class: "strong" }, s.title || s.host), " ",
        s.weak ? h("span", { class: "chip warn" }, "weak") : null),
      h("p", { class: "small muted address" }, `${s.host} · ${plural(s.sentences, "sentence")} · ${s.final_url || s.url}`),
      h("p", { class: "small" }, s.why),
      h("button", { type: "button", class: "btn small", onclick: () => light(ctx, [`S${s.n}`]) }, "Read it beside the article")))) : h("p", { class: "muted" }, "None yet.")));
  parts.push(h("section", {}, h("h4", {}, `Left out (${d.left_out.length})`),
    d.left_out.length ? h("ul", { class: "rows" }, d.left_out.map((s) => h("li", {},
      h("p", {}, h("span", { class: "strong" }, s.title || s.host || s.url)),
      h("p", { class: "small muted address" }, s.url),
      h("p", { class: "small" }, s.why)))) : h("p", { class: "muted" }, "Nothing was left out.")));
  if (d.summary.length) {
    parts.push(h("section", {}, h("h4", {}, "Research summary"), h("p", { class: "small muted" }, "Every statement cites the source sentences it came from."),
      h("ul", { class: "bullets" }, d.summary.map((item) => h("li", {}, item.text, " ",
        h("button", { type: "button", class: "chip cite", onclick: () => light(ctx, item.cites) }, describeLight(lightFor(item.cites))))))));
  }
  if (d.outline?.sections?.length) {
    parts.push(h("section", {}, h("h4", {}, "Outline"), h("p", { class: "strong" }, d.outline.title),
      h("ol", { class: "outline" }, d.outline.sections.map((sec) => h("li", {}, h("span", { class: "strong" }, sec.heading),
        sec.points?.length ? h("ul", { class: "bullets" }, sec.points.map((p) => h("li", {}, p))) : null)))));
  }
  return h("div", { class: "research" }, parts);
}

// --- versions and review -----------------------------------------------------------------------

function keptWords(r, current) {
  if (r.n === current) return "the current version";
  if (r.kept === 1) return "kept, then replaced";
  if (r.kept === 0) return "thrown away: it did not leave fewer open findings";
  return "being checked";
}

function versions(ctx, d, zone) {
  const reviews = d.checks.filter((c) => c.name === "review");
  const current = d.revision?.n;
  const parts = [h("section", {}, h("h4", {}, "The review's findings"),
    reviews.length ? reviews.map((c) => c.level === "ok" ? h("p", { class: "small" }, c.detail) : checkRow(ctx, d, c))
      : h("p", { class: "muted" }, "Not reviewed yet."))];
  parts.push(h("section", {}, h("h4", {}, `Versions (${d.revisions.length})`),
    d.revisions.length ? h("ol", { class: "rows versions" }, [...d.revisions].reverse().map((r) => h("li", { class: r.n === current ? "current" : "" },
      h("p", {}, h("span", { class: "strong" }, `Version ${r.n}`), `: ${KIND_WORDS[r.kind] ?? r.kind}, ${keptWords(r, current)}`,
        r.open_count !== null && r.open_count !== undefined ? ` · ${plural(r.open_count, "open finding")}` : "",
        h("span", { class: "small muted" }, ` · ${formatWhen(r.created, zone, d.now_ms)}`)),
      r.changes?.length ? h("ul", { class: "bullets small" }, r.changes.map((c) => h("li", {}, c)))
        : h("p", { class: "small muted" }, r.n === 1 ? "The first version." : "Its words did not change; only its marks may have."))))
      : h("p", { class: "muted" }, "No version yet.")));
  if (d.revision?.aside?.length) parts.push(h("p", { class: "small note" }, `Taken out of the current version when it was kept: ${d.revision.aside.join("; ")}.`));
  if (d.attempts.length) {
    parts.push(h("section", {}, h("h4", {}, "Publishing"), h("ul", { class: "rows" }, d.attempts.map((a) => h("li", {},
      h("p", { class: "small" }, `${formatWhen(a.started, zone, d.now_ms)} · ${a.publisher} · ${a.outcome}`), a.detail ? h("p", { class: "small muted" }, a.detail) : null)))));
  }
  return h("div", { class: "versions-tab" }, parts);
}
