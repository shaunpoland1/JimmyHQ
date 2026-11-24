// Scheduled: everything SocialBu has scheduled on the owner's account, whoever booked it,
// soonest first, grouped by day. Read only: no button here changes anything on SocialBu.
// It is read when the owner opens the view and when they press Refresh, never on a timer.
// What SocialBu says (its words, the text of a post) is put on the page as text only.

import { cutWords, emptyWords, groupByDay, readLine, skippedWords } from "../lib/scheduled.js";
import { h, say, why } from "../_ui.js";

/** Open the view: draw the kept list at once, then read SocialBu again. */
export async function enter(ctx) {
  const mine = ctx.state.scheduled;
  if (mine.busy) return;
  mine.busy = true;
  mine.problem = "";
  ctx.render();
  try {
    mine.data = await ctx.call("scheduled.kept", {});
    ctx.render();
    mine.data = await ctx.call("scheduled.refresh", {});
  } catch (error) {
    mine.problem = why(error);
  } finally {
    mine.busy = false;
    if (ctx.state.view === "scheduled") ctx.render();
  }
}

export function render(ctx) {
  const mine = ctx.state.scheduled;
  const data = mine.data;
  const refresh = h("button", { type: "button", class: "btn", disabled: mine.busy, onclick: () => enter(ctx) }, mine.busy ? "Reading…" : "Refresh");
  if (!data) {
    return h("div", {}, h("div", { class: "row wrap" }, h("h2", {}, "Scheduled on SocialBu"), refresh),
      mine.problem ? h("p", { class: "problem", role: "alert" }, mine.problem) : h("p", { class: "muted" }, "Reading SocialBu…"));
  }
  if (!data.usable) {
    return h("div", {}, h("h2", {}, "Scheduled on SocialBu"), h("div", { class: "banner", role: "note" }, data.said));
  }
  const groups = groupByDay(data.rows, { zone: ctx.ownerZone, nowMs: data.now_ms });
  const empty = emptyWords(data);
  const skipped = skippedWords(data.skipped);
  return h("div", { class: "scheduled" },
    h("div", { class: "row wrap" }, h("h2", {}, `Scheduled on SocialBu${data.rows.length ? ` (${data.rows.length})` : ""}`), refresh,
      h("span", { class: "small muted" }, `${readLine(data.read_ms, data.now_ms)} · times in ${ctx.ownerZone}`)),
    mine.problem ? h("p", { class: "problem", role: "alert" }, mine.problem) : null,
    data.failed ? h("div", { class: "banner bad", role: "note" }, data.failed.said) : null,
    data.cut ? h("div", { class: "banner", role: "note" }, cutWords(data)) : null,
    skipped ? h("p", { class: "small muted" }, skipped) : null,
    empty ? h("p", { class: "muted" }, empty) : null,
    groups.map((group) => h("section", { class: "group", "aria-label": group.label },
      h("h3", {}, group.label, h("span", { class: "small muted" }, ` · ${group.rows.length}`)),
      h("ol", { class: "rows" }, group.rows.map((row) => line(ctx, row))))));
}

function line(ctx, row) {
  const mine = ctx.state.scheduled;
  const opened = mine.open.has(row.id);
  const more = row.text.long ? h("button", {
    type: "button", class: "link small", "aria-expanded": opened ? "true" : "false",
    onclick: () => { if (opened) mine.open.delete(row.id); else mine.open.add(row.id); ctx.render(); },
  }, opened ? "Show less" : "Show all") : null;
  const jump = row.own?.link ? h("button", { type: "button", class: "btn small", onclick: () => ctx.go(row.own.link.view, row.own.link.where) }, row.own.link.words) : null;
  return h("li", { class: `srow${row.own ? " own" : ""}`, id: `scheduled-${row.id}` },
    h("div", { class: "when" }, h("span", { class: "strong" }, row.time), h("span", { class: "small muted" }, row.account)),
    h("div", { class: "what" },
      row.text.full ? h("p", { class: "ptext" }, opened ? row.text.full : row.text.first) : h("p", { class: "muted" }, "No text"),
      more,
      h("span", { class: "chips" },
        row.pictures ? h("span", { class: "chip" }, row.pictures) : null,
        row.own ? h("span", { class: "chip ok" }, row.own.words) : null)),
    h("div", { class: "row wrap" }, jump));
}
