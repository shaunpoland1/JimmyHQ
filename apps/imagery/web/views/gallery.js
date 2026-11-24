// The Gallery view: pictures newest first, with search, stars, and what to do with each.
import { ask, h, say, why } from "../_ui.js";
import { cleanSearch, galleryLine, likeThis, whenText } from "../lib/format.js";
import { saveAllLabel } from "../lib/saving.js";
import { lazyImage } from "../lib/thumbs.js";
import * as detail from "./detail.js";
import { saveAllStarred, saveDetails, savePicture } from "./saving.js";

let parts = null;
// The ids a "Save all starred" that stopped had already saved, so the next press goes on.
let resume = new Set();
// What the last "Save all starred" said, so a redraw of the page does not lose it.
let lastSaid = { text: "", bad: false };

function card(ctx, row) {
  const { data } = ctx.state;
  const given = row.kind === "given";
  const star = h("button", {
    type: "button", class: `btn small star${row.starred ? " on" : ""}`, "aria-pressed": row.starred ? "true" : "false",
    title: row.starred ? "Starred: never deleted to make room" : "Star it so it is never deleted to make room",
    onclick: async () => {
      try {
        const next = await ctx.call("picture.star", { id: row.id, on: !row.starred });
        Object.assign(row, next);
        // The count of starred pictures decides whether "Save all starred" is shown, and its words.
        ctx.state.data = { ...ctx.state.data, gallery: (await ctx.call("status", {})).gallery };
        ctx.render();
      } catch (error) {
        say(why(error), "bad");
      }
    },
  }, row.starred ? "★ Starred" : "☆ Star");
  return h("li", { class: "card" },
    h("button", { type: "button", class: "thumb", "aria-label": `Open: ${row.prompt}`, onclick: () => detail.open(ctx, row) },
      lazyImage(h, row.ref, row.prompt, 320)),
    h("p", { class: "prompt" }, given ? `Your picture: ${row.prompt}` : row.prompt),
    h("p", { class: "muted small" }, given ? whenText(row.created, ctx.zone)
      : `${row.provider} · ${row.model} · ${whenText(row.created, ctx.zone)}`),
    row.style ? h("p", {}, h("span", { class: "chip" }, row.style)) : null,
    h("div", { class: "row" },
      star,
      given ? null : h("button", { type: "button", class: "btn small", onclick: () => { ctx.state.form = likeThis(row, data); ctx.go("make"); } }, "More like this"),
      data.provider.supports_reference
        ? h("button", { type: "button", class: "btn small", onclick: () => { ctx.state.form.reference = row; ctx.go("make"); } }, "Use as starting picture")
        : null,
      h("button", { type: "button", class: "btn small", onclick: (event) => saving(event.currentTarget, () => savePicture(ctx, row)) }, "Save picture"),
      h("button", { type: "button", class: "btn small", onclick: (event) => saving(event.currentTarget, () => saveDetails(ctx, row)) }, "Save details"),
      h("button", { type: "button", class: "btn small danger", onclick: () => remove(ctx, row) }, "Delete")));
}

/** Run one save from a button's click, with the button off meanwhile, and say how it went. */
async function saving(button, work) {
  button.disabled = true;
  try {
    const done = await work();
    say(done.text, done.ok ? "ok" : "bad");
  } finally {
    button.disabled = false;
  }
}

async function saveAllClick(ctx, button, line) {
  button.disabled = true;
  try {
    const { result, text } = await saveAllStarred(ctx, resume);
    resume = result.left.length ? new Set([...resume, ...result.saved, ...result.skipped.map((one) => one.id)]) : new Set();
    button.textContent = saveAllLabel(ctx.state.data.gallery.starred, result.left.length);
    lastSaid = { text, bad: Boolean(result.stopped) };
  } catch (error) {
    lastSaid = { text: why(error), bad: true };
  } finally {
    line.textContent = lastSaid.text;
    line.className = `small ${lastSaid.bad ? "problem" : "muted"}`;
    button.disabled = false;
  }
}

export async function remove(ctx, row) {
  const yes = await ask("Delete this picture?",
    "It is deleted for good: the picture and its description are gone from this computer, starred or not. This cannot be undone.",
    "Delete", "danger");
  if (!yes) return;
  try {
    const done = await ctx.call("picture.delete", { id: row.id });
    say(done.said, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

function grid(ctx) {
  const { state } = ctx;
  if (!state.rows.length) {
    return h("div", { class: "empty" },
      h("p", {}, state.query || state.starred ? "No picture matches that." : "No pictures yet."),
      state.query || state.starred ? null : h("button", { type: "button", class: "btn primary", onclick: () => ctx.go("make") }, "Make the first one"));
  }
  return h("div", {},
    h("ul", { class: "grid", "aria-label": "Pictures" }, state.rows.map((row) => card(ctx, row))),
    state.rows.length < state.total
      ? h("div", { class: "row" }, h("button", { type: "button", class: "btn", onclick: async () => { await ctx.more(); ctx.render(); } },
        `Show more (${state.total - state.rows.length} left)`))
      : null);
}

export function render(ctx) {
  const { state } = ctx;
  const data = state.data;
  const search = h("input", { type: "search", value: state.query, placeholder: "Search descriptions", "aria-label": "Search descriptions", maxlength: 200 });
  let wait = null;
  search.addEventListener("input", () => {
    clearTimeout(wait);
    wait = setTimeout(async () => {
      state.query = cleanSearch(search.value);
      try {
        await ctx.reloadRows();
        update(ctx);
      } catch (error) {
        say(why(error), "bad");
      }
    }, 300);
  });
  const starred = h("input", { type: "checkbox", checked: state.starred, id: "starred-only" });
  starred.addEventListener("change", async () => {
    state.starred = starred.checked;
    try {
      await ctx.reloadRows();
      update(ctx);
    } catch (error) {
      say(why(error), "bad");
    }
  });
  const starredCount = data.gallery.starred;
  const saveLine = h("p", { class: `small ${lastSaid.bad ? "problem" : "muted"}`, role: "status" }, lastSaid.text);
  const saveButton = h("button", { type: "button", class: "btn", onclick: () => saveAllClick(ctx, saveButton, saveLine) },
    saveAllLabel(starredCount, resume.size ? Math.max(0, starredCount - resume.size) : 0));
  const saveAll = starredCount > 0
    ? h("div", { class: "row" }, saveButton,
      h("span", { class: "muted small grow" }, "JimmyHQ lets Imagery hand you only a few files from one click, and only so many a minute. When it stops, Imagery says so, and the button goes on from there."))
    : null;
  const body = h("div", { id: "grid" }, grid(ctx));
  const line = h("p", { class: "muted small" }, galleryLine(data.gallery));
  parts = { body, line };
  return h("div", {},
    h("h1", { class: "visually-hidden" }, "Gallery"),
    data.pruned ? h("div", { class: "banner warn" },
      h("p", { class: "small" }, `To stay inside your limits, Imagery deleted ${data.pruned.count} of your oldest pictures that were not starred, for good. Star the ones you want to keep, or raise the limits in settings.`),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn small", onclick: async () => { await ctx.call("pruned.dismiss", {}); await ctx.refresh(); } }, "Got it"),
        h("button", { type: "button", class: "btn small", onclick: () => ctx.openSettings() }, "Open settings"))) : null,
    h("div", { class: "row filters" }, search, h("label", { class: "check-line", for: "starred-only" }, starred, "Starred only")),
    saveAll, saveLine, line, body);
}

/** Redraw the grid and the fullness line after pictures arrive, leaving the search box alone. */
export function update(ctx) {
  if (!parts) return;
  parts.line.textContent = galleryLine(ctx.state.data.gallery);
  parts.body.replaceChildren(grid(ctx));
}
