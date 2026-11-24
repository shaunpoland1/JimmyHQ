// Publish: approved articles on their way to their sites. Publish, Published, "I checked,
// it is not there" for a publish with no answer, and the article copied as clean HTML or
// Markdown. A site on Practice sends nothing, and the page says so loudly.

import { COPY_BY_HAND, SAVE_NOTE, copiedSaid, copyText, fileName, saveRequest } from "../lib/exporting.js";
import { finished, headline, siteName, toPublish } from "../lib/lists.js";
import { EDIT_UNANSWERED, editUnanswered } from "../lib/sent.js";
import { formatWhen } from "../lib/time.js";
import { ask, dialog, field, h, say, why } from "../_ui.js";

export const PRACTICE = "Practice: nothing is being sent anywhere. Articles keeps what it would have sent and marks it sent. "
  + "To publish for real, choose By hand or WordPress for the site under Sites.";

function siteOf(ctx, id) {
  return ctx.data().sites.find((s) => s.id === id) ?? null;
}

export function render(ctx) {
  const data = ctx.data();
  const list = toPublish(data.pieces);
  const practice = [...new Set(list.map((p) => p.site_id))].map((id) => siteOf(ctx, id)).filter((s) => s?.publisher === "practice");
  const parts = [];
  if (practice.length || (!list.length && data.sites.some((s) => s.publisher === "practice"))) {
    const names = (practice.length ? practice : data.sites.filter((s) => s.publisher === "practice")).map((s) => s.name).join(", ");
    parts.push(h("div", { class: "banner practice", role: "note" }, h("p", { class: "strong" }, `${names}: ${PRACTICE}`),
      h("button", { type: "button", class: "btn small", onclick: () => ctx.go("sites") }, "Go to Sites")));
  }
  parts.push(h("p", { class: "small muted" }, SAVE_NOTE));
  if (!list.length) {
    parts.push(h("div", { class: "empty" }, h("p", {}, "Nothing is waiting to be published. Approved articles come here."),
      h("button", { type: "button", class: "btn", onclick: () => ctx.go("articles") }, "See the articles")));
  } else {
    parts.push(h("ul", { class: "rows" }, list.map((piece) => row(ctx, piece))));
  }
  const done = finished(data.pieces).filter((p) => p.state === "published");
  if (done.length) {
    parts.push(h("h2", {}, `Published (${done.length})`), h("ul", { class: "rows" }, done.map((piece) => h("li", { class: "pubrow" },
      h("div", { class: "what" }, h("span", { class: "strong" }, headline(piece)),
        h("span", { class: "small muted" }, `${siteName(data.sites, piece.site_id)} · ${formatWhen(piece.updated, ctx.zoneOfSite(piece.site_id), data.now_ms)}`)),
      h("div", { class: "row wrap" }, copyButtons(ctx, piece))))));
  }
  return h("div", { class: "publish" }, parts);
}

function row(ctx, piece) {
  const data = ctx.data();
  const site = siteOf(ctx, piece.site_id);
  const label = site?.publisher_label ?? "its site";
  const buttons = [];
  const act = (call, words, said, kind = "") => h("button", { type: "button", class: `btn ${kind}`.trim(), onclick: () => run(ctx, call, piece, said) }, words);
  if (piece.state === "approved") {
    buttons.push(h("button", { type: "button", class: "btn primary", onclick: () => publish(ctx, piece, site) },
      site?.sends_out ? `Publish to ${label}` : "Publish"));
  }
  if (piece.state === "unsure") {
    buttons.push(act("article.clear", "I checked, it is not there", "Cleared. It is approved again, and you can publish it again.", "primary"),
      act("article.published", "It is there: Published", "Marked published."));
  }
  if (piece.state === "sent") {
    if (editUnanswered(piece)) buttons.push(act("article.clear", "I checked, the edit is not there", "Cleared. Open the article to send your edit again."));
    buttons.push(h("button", { type: "button", class: "btn", onclick: () => ctx.open(piece.id) }, "Open to edit"));
    buttons.push(act("article.published", "Published", "Marked published.", "primary"));
  }
  const attempt = piece.attempt;
  return h("li", { class: `pubrow${piece.state === "unsure" ? " unsure" : ""}`, id: `pub-${piece.id}` },
    h("div", { class: "what" },
      h("button", { type: "button", class: "link strong", onclick: () => ctx.open(piece.id) }, headline(piece)),
      h("span", { class: "small muted" }, `${siteName(data.sites, piece.site_id)} · publishes ${site?.publisher === "practice" ? "nowhere (Practice)" : `by ${label}`} · ${piece.words}`),
      piece.state === "unsure" ? h("p", { class: "problem" }, "The site gave no answer, so the article may be there already. Look on the site first. Nothing is sent again until you say it is not there.") : null,
      editUnanswered(piece) ? h("p", { class: "problem" }, EDIT_UNANSWERED) : null,
      piece.error ? h("p", { class: piece.state === "unsure" ? "small muted" : "problem" }, piece.error) : null,
      attempt?.detail && attempt.detail !== piece.error ? h("p", { class: "small muted" }, `${formatWhen(attempt.finished ?? attempt.started, ctx.zoneOfSite(piece.site_id), data.now_ms)}: ${attempt.detail}`) : null),
    h("div", { class: "row wrap" }, buttons, copyButtons(ctx, piece)));
}

async function publish(ctx, piece, site) {
  if (site?.sends_out && !(await ask("Publish it?", `Articles sends this article to ${site.publisher_label} for ${site.name}, as a draft there. It is sent once; if no answer comes, it is never sent again by itself.`, "Publish"))) return;
  await run(ctx, "article.publish", piece, "Sent.");
}

async function run(ctx, call, piece, said) {
  try {
    const done = await ctx.call(call, { id: piece.id });
    say(done?.said || said, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

function copyButtons(ctx, piece) {
  const copies = [["html", "Copy as HTML"], ["markdown", "Copy as Markdown"]].map(([form, words]) => h("button", {
    type: "button", class: "btn small", onclick: () => copyOut(ctx, piece, form),
  }, words));
  return [...copies, h("button", { type: "button", class: "btn small", onclick: () => saveOut(ctx, piece) }, "Save as a file")];
}

/** The Markdown as a file the owner keeps. JimmyHQ saves it, and only right after this click. */
async function saveOut(ctx, piece) {
  try {
    const made = await ctx.call("article.export", { id: piece.id, form: "markdown" });
    const kept = await ctx.save(saveRequest(piece, made));
    say(`Saved as ${kept?.name ?? "a file"}. Your browser may ask where to keep it.`, "ok");
  } catch (error) {
    say(why(error), "bad");
  }
}

/** The browser's older way to copy: a hidden text box, selected, and the copy command. */
function copyCommand(text) {
  const box = h("textarea", { "aria-hidden": "true", class: "offscreen", value: text });
  document.body.appendChild(box);
  box.select();
  try {
    return document.execCommand("copy");
  } finally {
    box.remove();
  }
}

async function copyOut(ctx, piece, form) {
  let made;
  try {
    made = await ctx.call("article.export", { id: piece.id, form });
  } catch (error) {
    say(why(error), "bad");
    return;
  }
  const done = await copyText(made.text, { clipboard: globalThis.navigator?.clipboard, copyCommand });
  say(copiedSaid(done, form), done.ok ? "ok" : "warn");
  if (done.ok) return;
  const box = h("textarea", { rows: 14, value: made.text, class: "mono-text", "aria-label": fileName(made.name, piece.id, form) });
  setTimeout(() => { box.focus(); box.select(); }, 0);
  await dialog({ title: `Copy it as ${form === "markdown" ? "Markdown" : "HTML"}`, intro: copiedSaid(done, form),
    body: field(fileName(made.name, piece.id, form), box, COPY_BY_HAND), cancel: "Done" });
}
