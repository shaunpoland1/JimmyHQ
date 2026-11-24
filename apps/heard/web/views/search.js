// Search: every word you type must be in the line, in any order, and the last word may be
// only the start of one. The matched words are marked. Lines open their conversation.
import { fill, h, say, why } from "../_ui.js";
import { clock, dayHeading, plural, runs } from "../lib/format.js";

export async function show(ctx, root, current) {
  const state = ctx.state;
  const box = h("input", { type: "search", value: state.query, placeholder: "Words someone said", "aria-label": "Search what was said", autocomplete: "off", spellcheck: "false" });
  const results = h("div", {});
  let busy = false;

  async function run() {
    const query = box.value.trim();
    state.query = query;
    if (busy) return;
    if (!query) {
      state.found = null;
      draw();
      return;
    }
    busy = true;
    try {
      state.found = await ctx.call("search", { query });
    } catch (error) {
      say(why(error), "bad");
    }
    busy = false;
    draw();
  }

  function hit(item) {
    return h("button", { type: "button", class: "hit", onclick: () => ctx.openConversation(item.conv_id, item.segment_id) },
      h("span", { class: "meta muted small" }, `${item.heading}, ${dayHeading(item.day, ctx.state.overview.today)} at ${clock(item.start_ms, ctx.zone)}`),
      h("span", { class: "words" }, h("strong", {}, `${item.speaker}: `),
        runs(item.parts).map((r) => (r.hit ? h("mark", {}, r.text) : r.text))));
  }

  function draw() {
    const found = state.found;
    if (!found) {
      fill(results, h("p", { class: "muted" }, "Type a few words someone said and press Search. Only what was said is searched, not titles."));
      return;
    }
    fill(results,
      found.results.length
        ? h("p", { class: "muted small" }, found.full
          ? `The first ${found.results.length} matching lines. Add a word to narrow it down.`
          : plural(found.results.length, "matching line", "matching lines"))
        : h("p", { class: "muted" }, "No line has all of those words. Try fewer, or the start of a word."),
      found.fast ? null : h("p", { class: "muted small" }, "This computer has no fast search, so the lines are scanned one by one."),
      found.results.map(hit));
  }

  const form = h("form", { class: "search-bar" }, box,
    h("button", { type: "submit", class: "btn primary" }, "Search"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run();
  });
  fill(root, h("h1", { class: "visually-hidden" }, "Search"), form, results);
  draw();
  if (state.query && !state.found) await run();
  else if (!state.query) box.focus();
}
