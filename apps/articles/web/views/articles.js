// Articles: every article in a list beside the one open, with its sources. The keys move
// through the list as it is shown.

import { chooseWords, keyHint } from "../lib/keys.js";
import { GROUPS, checksLine, filterPieces, groupOf, headline, siteName } from "../lib/lists.js";
import { fill, h } from "../_ui.js";
import { detail } from "./detail.js";

const ORDER = { decide: 0, working: 1, publish: 2, done: 3 };

/** The articles the keys move through, in the order the list shows them. */
export function listed(ctx) {
  const rows = filterPieces(ctx.data().pieces, ctx.state.filters);
  return [...rows].sort((a, b) => ORDER[groupOf(a)] - ORDER[groupOf(b)]
    || (groupOf(a) === "decide" ? a.updated - b.updated : b.updated - a.updated) || a.id - b.id);
}

/** The words when there is no article at all: the same plain, centred empty page as Today, Publish and New. */
export const NONE_YET = "No articles yet. Start one under New.";

export function render(ctx) {
  const data = ctx.data();
  const state = ctx.state;
  // With nothing to list or open, the two-column layout would be an empty list beside an empty pane.
  if (!data.pieces.length) {
    return h("div", { class: "empty" }, h("p", {}, NONE_YET),
      h("button", { type: "button", class: "btn", onclick: () => ctx.go(data.sites.length ? "new" : "sites") }, data.sites.length ? "Start an article" : "Add a site first"));
  }
  const filters = state.filters;
  const results = h("div", { class: "picklist-wrap", "aria-live": "polite" });
  const draw = () => {
    const rows = listed(ctx);
    const parts = [];
    for (const [key, words] of GROUPS) {
      const group = rows.filter((p) => groupOf(p) === key);
      if (!group.length) continue;
      parts.push(h("h2", {}, `${words} (${group.length})`), h("ol", { class: "picklist" }, group.map((piece) => {
        const i = rows.indexOf(piece);
        return h("li", {}, h("button", {
          type: "button", class: `pick${piece.id === state.openPiece ? " current" : ""}${i === state.selected ? " cursor" : ""}`,
          "aria-current": piece.id === state.openPiece ? "true" : undefined,
          onclick: () => { state.selected = i; ctx.open(piece.id); },
        }, h("span", { class: "small muted" }, siteName(data.sites, piece.site_id)),
        h("span", { class: "strong" }, headline(piece)),
        h("span", { class: "chips" }, h("span", { class: `chip ${["held_coverage", "stopped_research", "stalled", "unsure"].includes(piece.state) ? "warn" : ""}`.trim() }, piece.words),
          piece.state === "ready" ? h("span", { class: `chip ${piece.questions ? "ask" : "ok"}` }, checksLine(piece)) : null)));
      })));
    }
    fill(results, parts.length ? parts : h("p", { class: "muted" }, "Nothing matches."));
  };
  const search = h("input", { type: "search", placeholder: "Search titles and topics", value: filters.query, "aria-label": "Search",
    oninput: () => { filters.query = search.value; draw(); } });
  const site = h("select", { "aria-label": "Site", onchange: () => { filters.site = site.value; draw(); } },
    h("option", { value: "" }, "Every site"), data.sites.map((s) => h("option", { value: String(s.id), selected: String(s.id) === filters.site }, s.name)));
  const group = h("select", { "aria-label": "Which articles", onchange: () => { filters.group = group.value; draw(); } },
    h("option", { value: "" }, "All articles"), GROUPS.map(([key, words]) => h("option", { value: key, selected: key === filters.group }, words)));
  draw();
  const letters = data.single_keys !== false;
  const hint = keyHint(letters);
  const side = h("div", { class: "side" }, h("div", { class: "filters stack" }, search, h("div", { class: "row" }, site, group)), results,
    h("p", { class: "keys small muted", "aria-label": "Keys" }, hint.keys),
    h("p", { class: "small muted" }, hint.note));
  const d = state.detail;
  const main = h("div", { class: "detail", id: "detail" },
    d && d.piece.id === state.openPiece ? detail(ctx, d)
      : h("p", { class: "muted pad" }, listed(ctx).length ? chooseWords(letters) : "Nothing open."));
  return h("div", { class: "split" }, side, main);
}
