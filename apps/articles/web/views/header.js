// An article's header image: the picture made last, what it shows, whether it ever leaves
// this computer, and the button that asks for one. Shown only when the owner switched
// header images on, for an article that is ready, approved or sent. A refusal is shown as
// the app's own sentence.

import { headerSaid, isPlaceholder, madeSaid, makeWords, showsHeader } from "../lib/header.js";
import { formatWhen } from "../lib/time.js";
import { h, say, why } from "../_ui.js";

export function headerSection(ctx, d) {
  const state = ctx.state;
  const { piece } = d;
  if (!showsHeader(ctx.data(), piece)) return null;
  const kept = state.header?.piece === piece.id ? state.header : null;
  const header = kept?.header ?? null;
  const problem = state.headerProblem?.piece === piece.id ? state.headerProblem.text : kept?.problem ?? "";
  const make = h("button", { type: "button", class: "btn", onclick: () => makeHeader(ctx, d, make) }, makeWords(header));
  const style = h("button", { type: "button", class: "btn small", onclick: () => {
    state.editingSite = piece.site_id;
    ctx.go("sites");
  } }, "Change this site's style guidance");
  return h("section", { class: "header-image", "aria-labelledby": "header-h" },
    h("h3", { id: "header-h" }, "Header image"),
    header ? picture(ctx, d, header) : null,
    h("p", { class: isPlaceholder(header) ? "banner practice strong" : "small muted", role: isPlaceholder(header) ? "note" : undefined },
      headerSaid(header, piece, d.site)),
    problem ? h("p", { class: "problem", role: "alert" }, problem) : null,
    h("div", { class: "row wrap" }, make, d.site ? style : null),
    h("p", { class: "small muted" }, "A model describes one picture from the title and the opening, under the site's style guidance, which is your own words. It never shows a real person."));
}

function picture(ctx, d, header) {
  const img = h("img", { class: "header-pic", alt: `Header image: ${header.description}` });
  const frame = h("div", { class: "header-frame" }, h("p", { class: "small muted" }, "Loading the picture…"));
  ctx.picture(header.ref).then((address) => {
    if (!address) {
      frame.replaceChildren(h("p", { class: "small note" }, "The picture is kept, but could not be shown here."));
      return;
    }
    img.src = address;
    frame.replaceChildren(img);
  });
  return h("figure", { class: "header-figure" }, frame,
    h("figcaption", { class: "small" }, h("span", { class: "strong" }, "What it shows: "), header.description,
      h("span", { class: "muted" }, ` · made ${formatWhen(header.created, ctx.zoneOfSite(d.piece.site_id), d.now_ms)} · ${header.width} by ${header.height}`)));
}

async function makeHeader(ctx, d, button) {
  const id = d.piece.id;
  button.disabled = true;
  ctx.state.headerProblem = null;
  say("Making a header image…");
  try {
    const made = await ctx.call("header.make", { id });
    ctx.state.header = { piece: id, header: made?.ref ? made : null };
    say(madeSaid(ctx.state.header.header), "ok");
  } catch (error) {
    ctx.state.headerProblem = { piece: id, text: why(error) };
    say(why(error), "bad");
  }
  if (ctx.state.openPiece === id && ctx.state.view === "articles") ctx.render();
}
