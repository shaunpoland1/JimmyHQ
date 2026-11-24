// Topics: what Reader looks for, who each is for, what happens to its articles, and
// how each is doing, with an offer to pause one that has gone quiet.
import { forWhom, healthLine, OUTCOME_WORDS, outcomesLine, quietOffer, verdict } from "../lib/health.js";
import { mergePhrasings } from "../lib/phrasings.js";
import { chipNames, fillName } from "../lib/topic_names.js";
import { lines } from "../lib/text.js";
import { ask, field, h, say, why } from "../_ui.js";

export function render(ctx) {
  const data = ctx.state.data;
  const editing = ctx.state.editing;
  return h("div", {},
    data.hint ? h("div", { class: "banner" }, h("p", {}, data.hint)) : null,
    editing ? form(ctx, editing === "new" ? null : data.topics.find((t) => t.id === editing)) :
      h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", onclick: () => { ctx.state.editing = "new"; ctx.render(); } }, "Add a topic")),
    researcherLine(data),
    h("ul", { class: "topics" }, data.topics.map((t) => item(ctx, t))));
}

function researcherLine(data) {
  const r = data.researcher;
  if (!r.held) return h("p", { class: "muted small" }, "Reader looks for every topic itself. An agent in the researcher role could look further; you can give the role to one on the Apps screen: open Reader, then Roles.");
  return h("p", { class: "muted small" }, `The researcher${r.label ? ` (${r.label})` : ""} looks for the topics that let it; Reader looks for the rest.`);
}

function item(ctx, t) {
  const now = Date.now();
  const offer = quietOffer(t);
  const said = verdict(t.health);
  return h("li", {},
    h("div", { class: "row spread" },
      h("div", { class: "grow" },
        h("h3", {}, t.name, t.paused ? h("span", { class: "chip" }, "Paused") : null, said ? h("span", { class: "chip" }, said) : null),
        h("p", { class: "small" }, forWhom(t)),
        h("p", { class: "muted small" }, `Searches: ${t.phrasings.join(" · ")}`),
        h("p", { class: "muted small" }, `${outcomesLine(t.outcomes)}. ${t.covered_by === "researcher" ? "The researcher looks for it." : "Reader looks for it itself."}`),
        h("p", { class: "small" }, healthLine(t.health, now))),
      h("div", { class: "row end" },
        h("button", { type: "button", class: "btn small", onclick: () => { ctx.state.editing = t.id; ctx.render(); } }, "Edit"),
        h("button", { type: "button", class: "btn small", onclick: () => pause(ctx, t, !t.paused) }, t.paused ? "Resume" : "Pause"))),
    offer ? h("div", { class: "banner warn" }, h("p", { class: "small" }, offer),
      h("div", { class: "row" },
        h("button", { type: "button", class: "btn small primary", onclick: () => pause(ctx, t, true) }, "Pause it"),
        h("button", { type: "button", class: "btn small", onclick: () => { ctx.state.editing = t.id; ctx.render(); } }, "Change its phrasings"))) : null);
}

async function pause(ctx, t, paused) {
  try {
    await ctx.call("topics.pause", { id: t.id, paused });
    say(paused ? `${t.name} is paused. Nothing is searched for it until you resume it.` : `${t.name} is searched again.`, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

function form(ctx, topic) {
  const defaults = topic?.outcomes ?? [ctx.state.data.limits.default_outcome];
  const name = h("input", { value: topic?.name ?? "", maxlength: 80, required: true });
  const phrasings = h("textarea", { rows: 3, value: (topic?.phrasings ?? []).join("\n"), placeholder: "What you would type into a search box, one per line" });
  const forOwner = h("input", { type: "radio", name: "for", checked: !topic?.audience });
  const forOthers = h("input", { type: "radio", name: "for", checked: Boolean(topic?.audience) });
  const audience = h("input", { value: topic?.audience ?? "", maxlength: 300, placeholder: "Such as: people who follow my garden page" });
  const outcomes = Object.entries(OUTCOME_WORDS).map(([id, words]) => [id, h("input", { type: "checkbox", checked: defaults.includes(id) }), words]);
  const researcher = h("input", { type: "checkbox", checked: topic ? Boolean(topic.researcher) : true });
  const problem = h("p", { class: "problem", role: "alert" });
  const suggestButton = h("button", { type: "button", class: "btn small", onclick: suggest }, "Suggest phrasings");
  const suggestNote = h("p", { class: "muted small", role: "status" });
  async function suggest() {
    suggestNote.textContent = "";
    if (!name.value.trim()) {
      suggestNote.textContent = "Type the topic's name first, then Reader can suggest phrasings for it.";
      return;
    }
    suggestButton.disabled = true;
    suggestButton.textContent = "Thinking of phrasings…";
    try {
      const got = await ctx.call("topics.suggest", {
        name: name.value, audience: forOthers.checked ? audience.value : "", phrasings: lines(phrasings.value),
      });
      const merged = mergePhrasings(phrasings.value, got.phrasings);
      phrasings.value = merged.text;
      suggestNote.textContent = merged.added
        ? `Added ${merged.added} to the box. Edit them as you like; nothing is saved until you press Save.`
        : "Those were all in the box already.";
    } catch (error) {
      suggestNote.textContent = why(error);
    } finally {
      suggestButton.disabled = false;
      suggestButton.textContent = "Suggest phrasings";
    }
  }
  // Suggested names (new topics only): chips under the Name field. Nothing is saved by them.
  const nameButton = topic ? null : h("button", { type: "button", class: "btn small", onclick: suggestNames }, "Suggest topics");
  const nameNote = h("p", { class: "muted small", role: "status" },
    topic ? "" : "Suggestions get better once there are topics, and once you have kept some articles.");
  const chips = h("div", { class: "row" });
  let filled = null;
  async function suggestNames() {
    nameNote.textContent = "";
    nameButton.disabled = true;
    nameButton.textContent = "Thinking of topics…";
    try {
      const got = await ctx.call("topics.suggest_names", {});
      const names = chipNames(got.names, ctx.state.data.topics.map((t) => t.name));
      chips.replaceChildren(...names.map((text) => h("button", { type: "button", class: "chip", onclick: () => {
        const next = fillName(name.value, filled, text);
        name.value = next.name;
        filled = next.filled;
        if (!next.changed) nameNote.textContent = "Your own name was left as it is. Clear the Name box to use this one.";
        else nameNote.textContent = "";
      } }, text)));
      nameNote.textContent = names.length
        ? "Choose one to put it in the Name box. Nothing is saved until you press Save."
        : "Those were all topics you already follow. Try again for others.";
    } catch (error) {
      nameNote.textContent = why(error);
    } finally {
      nameButton.disabled = false;
      nameButton.textContent = "Suggest topics";
    }
  }
  async function save() {
    problem.textContent = "";
    const values = {
      name: name.value, phrasings: lines(phrasings.value),
      audience: forOthers.checked ? audience.value : "",
      outcomes: outcomes.filter(([, box]) => box.checked).map(([id]) => id),
      researcher: researcher.checked, paused: topic ? Boolean(topic.paused) : false,
    };
    if (topic) values.id = topic.id;
    try {
      await ctx.call("topics.save", values);
      ctx.state.editing = null;
      say(`${values.name.trim()} is saved.`, "ok");
      await ctx.refresh();
    } catch (error) {
      problem.textContent = why(error);
    }
  }
  async function remove() {
    if (!(await ask(`Remove ${topic.name}?`, "Its articles stay in your lists. Nothing more is searched for it.", "Remove", "danger"))) return;
    try {
      await ctx.call("topics.remove", { id: topic.id });
      ctx.state.editing = null;
      say(`${topic.name} is removed.`, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
  }
  return h("div", { class: "form" },
    h("h2", {}, topic ? `Change ${topic.name}` : "A new topic"),
    field("Name", name),
    topic ? null : h("div", { class: "row" }, nameButton, nameNote),
    topic ? null : chips,
    field("Search phrasings", phrasings, "Reader searches with each in turn."),
    h("div", { class: "row" }, suggestButton, suggestNote),
    h("fieldset", {}, h("legend", {}, "Who it is for"),
      h("label", { class: "check-line" }, forOwner, "Me"),
      h("label", { class: "check-line" }, forOthers, "Others, described in a sentence"),
      audience),
    h("fieldset", {}, h("legend", {}, "What happens to an article found for it"),
      outcomes.map(([, box, words]) => h("label", { class: "check-line" }, box, words)),
      h("p", { class: "muted small" }, ctx.state.data.mining_on ? "" : "Mining stays off until you switch on \"Mine articles into Memory\" in Reader's settings.")),
    h("label", { class: "check-line" }, researcher, "Let the researcher look for it, when an agent holds that role"),
    problem,
    h("div", { class: "row end" },
      topic ? h("button", { type: "button", class: "btn danger", onclick: remove }, "Remove") : null,
      h("button", { type: "button", class: "btn", onclick: () => { ctx.state.editing = null; ctx.render(); } }, "Cancel"),
      h("button", { type: "button", class: "btn primary", onclick: save }, "Save")));
}
