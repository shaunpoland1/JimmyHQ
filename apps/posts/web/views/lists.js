// Today, Went out and Dropped: lists that lead to the piece behind each line.

import { dropped, filterRows, headline, isPracticePost, pageName, wentOut } from "../lib/lists.js";
import { formatSlot, relative } from "../lib/time.js";
import { NOTHING, needs, stuckSteps, underWay } from "../lib/today.js";
import { fill, h } from "../_ui.js";

function stuckSection(items, ctx) {
  if (!items.length) return null;
  return h("section", { class: "group stuck", "aria-labelledby": "g-stuck-steps" },
    h("h2", { id: "g-stuck-steps" }, "Work that is not moving"),
    h("ul", { class: "rows" }, items.map((item) => h("li", {},
      h("button", { type: "button", class: "line", onclick: () => ctx.go(item.go.view, item.go) },
        h("span", { class: "strong" }, item.text),
        h("span", { class: "small muted" }, item.detail))))));
}

export function today(ctx) {
  const data = ctx.data();
  const groups = needs(data);
  const quiet = underWay(data);
  const stuck = stuckSection(stuckSteps(data), ctx);
  if (!groups.length) {
    return h("div", { class: stuck ? "today" : "empty" },
      stuck ? null : h("p", { class: "big" }, NOTHING),
      stuck ?? null,
      !stuck && quiet ? h("p", { class: "muted" }, quiet) : null,
      h("button", { type: "button", class: "btn", onclick: () => ctx.go("new") }, "Start a post"));
  }
  return h("div", { class: "today" },
    groups.map((group) => h("section", { class: `group ${group.key}`, "aria-labelledby": `g-${group.key}` },
      h("h2", { id: `g-${group.key}` }, group.title),
      h("ul", { class: "rows" }, group.items.map((item) => h("li", {},
        h("button", { type: "button", class: "line", onclick: () => ctx.go(item.go.view, item.go) },
          h("span", { class: "strong" }, item.text),
          item.detail ? h("span", { class: "small muted" }, item.detail) : null)))))),
    stuck,
    quiet ? h("p", { class: "small muted" }, quiet) : null);
}

/** A searchable list. Typing re-draws only the results, so the search box keeps its place. */
function searchable(ctx, key, rows, line, empty) {
  const data = ctx.data();
  const filters = ctx.state.filters[key];
  const results = h("ol", { class: "rows", "aria-live": "polite" });
  const count = h("p", { class: "small muted" });
  const draw = () => {
    const found = filterRows(rows, filters);
    count.textContent = rows.length ? `${found.length} of ${rows.length}` : "";
    fill(results, found.length ? found.map(line) : h("li", { class: "muted" }, rows.length ? "Nothing matches." : empty));
  };
  const search = h("input", { type: "search", placeholder: "Search the text", value: filters.query, "aria-label": "Search",
    oninput: () => { filters.query = search.value; draw(); } });
  const page = h("select", { "aria-label": "Brand", onchange: () => { filters.page = page.value; draw(); } },
    h("option", { value: "" }, "Every brand"), data.pages.map((p) => h("option", { value: String(p.id), selected: String(p.id) === filters.page }, p.name)));
  draw();
  return h("div", {}, h("div", { class: "row wrap filters" }, search, page, count), results);
}

export function went(ctx) {
  const data = ctx.data();
  return searchable(ctx, "went", wentOut(data.posts), (post) => h("li", {},
    h("button", { type: "button", class: "line", onclick: () => ctx.open(post.piece_id) },
      h("span", { class: "small muted" }, `${formatSlot(post.slot_ms ?? post.updated, ctx.zoneOfPage(post.page_id), data.now_ms)} · ${pageName(data.pages, post.page_id)}`),
      h("span", { class: "strong" }, headline(post)),
      isPracticePost(post) ? h("span", { class: "chip warn" }, "Practice") : null,
      post.title ? h("span", { class: "small" }, post.body) : null,
      isPracticePost(post) && post.note ? h("span", { class: "small muted" }, post.note) : null)),
  "Nothing has gone out yet.");
}

export function droppedList(ctx) {
  const data = ctx.data();
  return searchable(ctx, "dropped", dropped(data.posts, data.pieces), (row) => h("li", {},
    h("button", { type: "button", class: "line", onclick: () => ctx.open(row.piece_id) },
      h("span", { class: "small muted" }, `${relative(row.when, data.now_ms)} · ${pageName(data.pages, row.page_id)}`),
      h("span", { class: "strong" }, row.title || headline(row)),
      row.note ? h("span", { class: "small" }, row.note) : null)),
  "Nothing has been dropped or declined.");
}
