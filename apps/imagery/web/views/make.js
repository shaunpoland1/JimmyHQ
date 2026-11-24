// The Make view: describe a picture, choose its shape and how many, and watch each request.
import { field, h, say, why } from "../_ui.js";
import { countChoices, makeProblem, runActive, runLine, todayLine } from "../lib/format.js";
import { lazyImage } from "../lib/thumbs.js";

let live = null; // the parts of the view that change while pictures are being made

function runsList(ctx) {
  const runs = ctx.state.data.runs;
  if (!runs.length) return null;
  return h("ul", { class: "runs", "aria-label": "Requests" }, runs.map((run) => h("li", { class: `run ${run.state}` },
    h("div", { class: "grow" },
      h("p", { class: "run-prompt" }, run.prompt),
      h("p", { class: run.state === "failed" ? "problem small" : "muted small", role: runActive(run) ? undefined : "status" }, runLine(run))),
    runActive(run)
      ? h("button", { type: "button", class: "btn small", onclick: () => stop(ctx, run) }, "Stop")
      : h("span", { class: "row" },
        run.made ? h("button", { type: "button", class: "btn small", onclick: () => ctx.go("gallery") }, "See in gallery") : null,
        h("button", { type: "button", class: "btn small", onclick: () => dismiss(ctx, run) }, "Dismiss")))));
}

async function stop(ctx, run) {
  try {
    await ctx.call("run.stop", { id: run.id });
    say("Stopping after the picture being made.", "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

async function dismiss(ctx, run) {
  try {
    await ctx.call("run.dismiss", { id: run.id });
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}

function referenceBox(ctx) {
  const { state } = ctx;
  if (!state.data.provider.supports_reference) return null;
  const chosen = state.form.reference;
  const input = h("input", { type: "file", accept: "image/png,image/jpeg,image/webp", "aria-label": "Starting picture" });
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const kept = await ctx.upload(file);
      state.form.reference = await ctx.call("reference.add", { ref: kept.ref, name: kept.name });
      say("Starting picture added. It is also in your gallery.", "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
  });
  return h("fieldset", {},
    h("legend", {}, "Start from a picture (optional)"),
    chosen
      ? h("div", { class: "row" },
        h("div", { class: "thumb small" }, lazyImage(h, chosen.ref, "The starting picture", 120)),
        h("span", { class: "muted small grow" }, chosen.prompt ? `Changing: ${chosen.prompt}` : "Changing the picture you chose."),
        h("button", { type: "button", class: "btn small", onclick: () => { state.form.reference = null; ctx.render(); } }, "Take it out"))
      : h("div", {},
        h("p", { class: "muted small" }, "The service changes this picture as your description says. Pick one here, or press \"Use as starting picture\" on one in the gallery."),
        input));
}

export function render(ctx) {
  const { state } = ctx;
  const data = state.data;
  const form = state.form;
  const problem = data.provider.problem;
  const prompt = h("textarea", { rows: 4, maxlength: data.prompt_max, placeholder: "Such as: A red kite over a green hill at sunset", "aria-label": "What the picture should show" });
  prompt.value = form.prompt;
  prompt.addEventListener("input", () => { form.prompt = prompt.value; syncButton(); });
  const style = h("select", {}, h("option", { value: "" }, "None"),
    data.presets.map((p) => h("option", { value: p.name, selected: p.name === form.style, title: p.ending }, p.name)));
  style.addEventListener("change", () => { form.style = style.value; });
  const aspect = h("select", {}, data.provider.aspects.map((a) => h("option", { value: a, selected: a === form.aspect }, a)));
  aspect.addEventListener("change", () => { form.aspect = aspect.value; });
  const count = h("select", {}, countChoices(data.max_count).map((n) => h("option", { value: String(n), selected: n === form.count }, String(n))));
  count.addEventListener("change", () => { form.count = Number(count.value); });
  const send = h("button", { type: "button", class: "btn primary", onclick: submit }, "Make");
  const why_ = h("p", { class: "muted small", role: "status" });

  function syncButton() {
    const blocked = makeProblem(form, data);
    send.disabled = state.busy || Boolean(blocked);
    why_.textContent = blocked && !problem ? blocked : "";
  }

  async function submit() {
    state.busy = true;
    syncButton();
    try {
      const done = await ctx.call("make", {
        prompt: form.prompt, style: form.style, aspect: form.aspect, count: form.count,
        reference: form.reference ? form.reference.id : null,
      });
      say(done.said, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    } finally {
      state.busy = false;
      syncButton();
    }
  }

  const today = h("p", { class: "muted small", id: "today" }, todayLine(data.today, data.provider.outside));
  const runs = h("div", { id: "runs" }, runsList(ctx));
  live = { today, runs };
  syncButton();
  return h("div", {},
    h("h1", { class: "visually-hidden" }, "Make a picture"),
    problem ? h("div", { class: "banner warn" },
      h("p", { class: "small" }, problem),
      h("button", { type: "button", class: "btn small", onclick: () => ctx.openSettings() }, "Open settings")) : null,
    h("div", { class: "form" },
      h("h2", {}, "Make a picture"),
      h("p", { class: "muted small" },
        `Image service: ${data.provider.label}${data.provider.outside ? `, model ${data.provider.model}` : ". It draws a labelled placeholder on this computer and sends nothing"}. `,
        h("button", { type: "button", class: "link", onclick: () => ctx.openSettings() }, "Change it in settings")),
      field("What should the picture show?", prompt),
      h("div", { class: "row" },
        field("Style", style, "Words added to the end of your description."),
        field("Shape", aspect),
        field("How many", count, `Up to ${data.max_count}.`)),
      referenceBox(ctx),
      h("div", { class: "row end" }, why_, send),
      today),
    runs);
}

/** Redraw only what changes while pictures are made, so typing is never interrupted. */
export function update(ctx) {
  if (!live) return;
  live.today.textContent = todayLine(ctx.state.data.today, ctx.state.data.provider.outside);
  live.runs.replaceChildren();
  const list = runsList(ctx);
  if (list) live.runs.appendChild(list);
}

