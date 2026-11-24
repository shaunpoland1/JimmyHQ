// Offered: the articles kept for other apps, in the shape they will read them.
import { clip, when } from "../lib/text.js";
import { takeBack } from "../status.js";
import { h } from "../_ui.js";

export function render(ctx) {
  const list = ctx.state.offered ?? [];
  return h("div", {},
    h("p", { class: "muted small" }, "Articles you sent on, or that a topic offers. An app you let read Reader's Articles, such as Posts, takes them from here; the Apps screen says which apps do. Each one reads the article itself. Take one back and it leaves this list; an app that also reads Reader's \"Offers taken back\" is told, and can drop what it took."),
    postedLine(ctx.state.data?.posted_feed),
    list.length
      ? h("ul", { class: "rows" }, list.map((o) => h("li", {},
        h("strong", {}, o.title),
        h("p", { class: "small" }, clip(o.why, 400)),
        h("p", { class: "muted small" }, `${o.topic || "No topic"} · ${when(o.when_ms, ctx.zone)}`),
        h("p", { class: "address" }, o.url),
        h("button", { type: "button", class: "btn small", onclick: () => takeBack(ctx, o.article) }, "Take it back"))))
      : h("p", { class: "muted pad" }, "Nothing has been offered yet. Press Send on in Triage or Articles to offer an article."));
}

/** One quiet line on Posts' feed of what went out, when Reader may read it. */
function postedLine(feed) {
  if (!feed?.reads) return null;
  if (feed.ok) return h("p", { class: "muted small" }, "Articles close to something Posts already posted are marked so in Articles.");
  return h("p", { class: "muted small" }, `Reader cannot mark articles close to something already posted: ${feed.said}`);
}
