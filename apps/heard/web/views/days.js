// Conversations by day: when each started, its title, how long it ran and who spoke.
import { fill, h, say, why } from "../_ui.js";
import { addPage, clock, dayHeading, groupByDay, lengthWords, plural, whoWords } from "../lib/format.js";
import { setupSteps } from "./settings.js";

function row(ctx, item) {
  const who = whoWords(item.speakers, item.speaker_count);
  return h("li", {},
    h("button", { type: "button", class: "open", onclick: () => ctx.openConversation(item.id) },
      h("span", { class: "name" }, item.heading),
      h("span", { class: "meta" },
        h("span", {}, clock(item.started_ms, ctx.zone)),
        h("span", {}, lengthWords(item.seconds)),
        h("span", {}, plural(item.words, "word", "words")),
        who ? h("span", {}, who) : null,
        item.open ? h("span", { class: "live" }, "Still listening") : null,
        item.has_summary ? h("span", { class: "chip" }, "Summary") : null)));
}

export async function show(ctx, root, current) {
  const first = await ctx.call("days", {});
  if (!current()) return;
  const view = { days: groupByDay(first.conversations), next: first.next, busy: false };
  const overview = ctx.state.overview;

  async function more() {
    view.busy = true;
    draw();
    try {
      const page = await ctx.call("days", { before: view.next });
      if (!current()) return;
      view.days = addPage(view.days, page.conversations);
      view.next = page.next;
    } catch (error) {
      say(why(error), "bad");
    }
    view.busy = false;
    draw();
  }

  function draw() {
    if (!view.days.length) {
      fill(root, h("div", { class: "empty" },
        h("p", { class: "big" }, overview.connected ? "No conversations are kept." : "Nothing has been heard yet."),
        overview.connected
          ? h("p", { class: "muted" }, "Messages have arrived, but none held words to keep. Settings shows what was refused and why.")
          : [h("p", { class: "muted" }, "Connect your recorder once and its conversations appear here."), setupSteps()],
        h("button", { type: "button", class: "btn", onclick: () => ctx.go("settings") }, "Open settings")));
      return;
    }
    fill(root,
      h("h1", { class: "visually-hidden" }, "Conversations"),
      view.days.map((d) => h("section", {},
        h("h2", { class: "day-heading" }, dayHeading(d.day, overview.today)),
        h("ul", { class: "convos" }, d.items.map((item) => row(ctx, item))))),
      view.next
        ? h("div", { class: "row" }, h("button", { type: "button", class: "btn", disabled: view.busy, onclick: more }, view.busy ? "Loading..." : "Earlier"))
        : null);
  }
  draw();
}
