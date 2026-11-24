// Add a package, and the carrier picker both forms use.
import { addProblem, carrierFrom, carrierOptions, choiceOf, OTHER } from "../lib/form.js";
import { ask, field, h, say, why } from "../_ui.js";

/**
 * The carrier picker: a choice, and under it the box for another code and a name to save
 * it under. `read()` gives {carrier, carrier_name} to send.
 */
export function carrierPicker(ctx, row) {
  const data = ctx.state.data;
  const options = carrierOptions(data.carriers, data.tracker);
  const select = h("select", {}, options.map((o) => h("option", { value: o.value, selected: o.value === (row ? choiceOf(row, data.carriers) : "") }, o.label)));
  const code = h("input", { value: row && choiceOf(row, data.carriers) === OTHER ? row.carrier : "", inputmode: "numeric", placeholder: "Carrier code", maxlength: 40 });
  const name = h("input", { value: "", maxlength: 40, placeholder: "Name to save it under (optional)" });
  const more = h("div", { class: "row" }, field("Carrier code", code), field("Save it as", name));
  const show = () => { more.hidden = select.value !== OTHER; };
  select.addEventListener("change", show);
  show();
  return {
    node: h("div", {}, field("Carrier", select, data.tracker.carrier_help), more),
    read: () => carrierFrom(select.value, code.value, name.value),
  };
}

export function render(ctx) {
  const label = h("input", { maxlength: 80, required: true, placeholder: "Such as: Garden hose" });
  const number = h("input", { maxlength: 80, required: true, autocomplete: "off", spellcheck: "false" });
  const picker = carrierPicker(ctx, null);
  const problem = h("p", { class: "problem", role: "alert" });
  const send = h("button", { type: "button", class: "btn primary", onclick: save }, "Add package");
  async function save() {
    problem.textContent = addProblem({ label: label.value, number: number.value });
    if (problem.textContent) return;
    send.disabled = true;
    send.textContent = "Adding…";
    try {
      const added = await ctx.call("package.add", { label: label.value, number: number.value, ...picker.read() });
      ctx.state.adding = false;
      say(added.said, "ok");
      await ctx.refresh();
    } catch (error) {
      problem.textContent = why(error);
      send.disabled = false;
      send.textContent = "Add package";
    }
  }
  return h("div", { class: "form" },
    h("h2", {}, "Add a package"),
    field("Name", label, "Something to tell it apart by. This is what a card on your Bench says."),
    field("Tracking number", number),
    picker.node,
    problem,
    h("div", { class: "row end" },
      h("button", { type: "button", class: "btn", onclick: () => { ctx.state.adding = false; ctx.render(); } }, "Cancel"),
      send),
    saved(ctx));
}

/** The carriers saved under a name, each with a way to forget it. */
function saved(ctx) {
  const carriers = ctx.state.data.carriers;
  if (!carriers.length) return null;
  return h("details", {},
    h("summary", {}, "Carriers you saved"),
    h("ul", { class: "rows" }, carriers.map((c) => h("li", { class: "row spread" },
      h("span", {}, `${c.name} (code ${c.code})`),
      h("button", { type: "button", class: "btn small", onclick: () => forget(ctx, c) }, "Forget")))));
}

async function forget(ctx, carrier) {
  if (!(await ask(`Forget ${carrier.name}?`, "It leaves the picker. Packages that use it keep it.", "Forget", "danger"))) return;
  try {
    const done = await ctx.call("carrier.forget", { carrier: carrier.code });
    say(done.said, "ok");
    await ctx.refresh();
  } catch (error) {
    say(why(error), "bad");
  }
}
