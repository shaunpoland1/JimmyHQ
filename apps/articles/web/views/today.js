// Today: what needs the owner, each line leading to where it is dealt with.

import { NOTHING, needs, underWay } from "../lib/today.js";
import { h } from "../_ui.js";

export function render(ctx) {
  const data = ctx.data();
  const groups = needs(data);
  const quiet = underWay(data);
  if (!groups.length) {
    return h("div", { class: "empty" }, h("p", { class: "big" }, NOTHING),
      quiet ? h("p", { class: "muted" }, quiet) : null,
      h("button", { type: "button", class: "btn", onclick: () => ctx.go("new") }, "Start an article"));
  }
  return h("div", { class: "today" },
    groups.map((group) => h("section", { class: `group ${group.key}`, "aria-labelledby": `g-${group.key}` },
      h("h2", { id: `g-${group.key}` }, group.title),
      h("ul", { class: "rows" }, group.items.map((item) => h("li", {},
        h("button", { type: "button", class: "line", onclick: () => ctx.go(item.go.view, item.go) },
          h("span", { class: "strong" }, item.text),
          item.detail ? h("span", { class: "small muted" }, item.detail) : null)))))),
    quiet ? h("p", { class: "small muted" }, quiet) : null);
}
