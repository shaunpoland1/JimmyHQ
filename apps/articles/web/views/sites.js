// Sites: who Articles writes for. A name, who it writes for, how it sounds and writing
// samples (one row each), the shape an article usually takes in the owner's own words,
// where it publishes, and the time zone its times are read in. A WordPress site names one
// of the owner's own addresses, which he gives Articles under Settings, Apps, Articles,
// with its user name and application password: none of the three is typed on this page.
// When header images are switched on, the site also keeps how its pictures should look.
// The form can be filled in from the site's own website: the proposals go into the empty
// fields only, and nothing is saved until the owner presses Save.

import { NO_HOSTS, OPEN_SETTINGS, SETTINGS_KEPT, addressOf, goneSaid, hostChoices, hostGone, hostOf, siteFromForm, splitSamples, styleChange } from "../lib/forms.js";
import { FILL_FIELDS, addressFrom, fillEmpty, fillSaid } from "../lib/fill.js";
import { field, fill, h, say, why } from "../_ui.js";
import { zonePicker } from "./zonePicker.js";

export const FIRST_RUN = "Articles writes for a site: add your first one, so it knows who it writes for, how it sounds and where it publishes.";

const PUBLISH_HELP = {
  practice: "Practice publishes nothing: it keeps what it would have sent, so you can try everything first.",
  by_hand: "By hand: you copy the finished article as HTML or Markdown, put it on your site yourself, then press Published.",
  wordpress: "WordPress: Articles sends each approved article as a draft to one of your own addresses when you press Publish; you publish it there. "
    + "The address, its user name and its application password are set under Settings, Apps, Articles.",
};

export function render(ctx) {
  const data = ctx.data();
  const hosts = data.hosts ?? [];
  const editing = ctx.state.editingSite;
  const parts = [];
  if (!data.sites.length) parts.push(h("p", { class: "banner" }, FIRST_RUN));
  if (editing !== null) {
    parts.push(siteForm(ctx, editing === "new" ? null : data.sites.find((s) => s.id === editing) ?? null));
  } else {
    parts.push(h("div", { class: "row" }, h("button", { type: "button", class: "btn primary", onclick: () => { ctx.state.editingSite = "new"; ctx.render(); } }, "Add a site")));
  }
  parts.push(h("ul", { class: "cards" }, data.sites.map((site) => h("li", { class: "card" },
    h("div", { class: "row spread" }, h("h3", {}, site.name),
      h("button", { type: "button", class: "btn small", onclick: () => { ctx.state.editingSite = site.id; ctx.render(); } }, "Edit")),
    site.audience ? h("p", {}, `For ${site.audience}`) : h("p", { class: "muted" }, "Who it writes for is not said yet."),
    site.register ? h("p", { class: "small" }, site.register) : null,
    hostGone(site, hosts) ? h("p", { class: "gone" }, goneSaid(hostGone(site, hosts))) : null,
    h("p", { class: "small muted" }, [`Publishes: ${site.publisher_label}`, site.publisher === "wordpress" ? hostOf(site.wordpress_url) || "no address chosen" : null,
      `${splitSamples(site.samples).length} writing sample${splitSamples(site.samples).length === 1 ? "" : "s"}`,
      site.timezone || `your time zone (${ctx.ownerZone})`].filter(Boolean).join(" · "))))));
  return h("div", { class: "sites" }, parts);
}

function siteForm(ctx, site) {
  const v = (x) => x ?? "";
  const changed = () => { ctx.state.typed = true; };
  const name = h("input", { type: "text", maxlength: 120, value: v(site?.name), required: true });
  const audience = h("input", { type: "text", maxlength: 500, value: v(site?.audience), placeholder: "People new to growing their own food" });
  const register = h("textarea", { rows: 2, maxlength: 1000, value: v(site?.register), placeholder: "Warm, plain and practical. Short paragraphs." });
  const samples = sampleRows(splitSamples(site?.samples), changed);
  const length = h("input", { type: "text", maxlength: 300, value: v(site?.length), placeholder: "About 1,200 words" });
  const headings = h("input", { type: "text", maxlength: 300, value: v(site?.headings), placeholder: "A short opening with no heading, then four or five headed sections" });
  const closing = h("textarea", { rows: 2, maxlength: 1000, value: v(site?.closing), placeholder: "End with a line on where readers can learn more" });
  const publishers = ctx.data().publishers;
  const help = h("span", { class: "help" });
  const wordpress = h("div", { class: "stack" });
  // The saved address, only for a site that publishes to WordPress now: a site switching to it has none.
  const kept = site?.publisher === "wordpress" ? site.wordpress_url : "";
  let hostList = ctx.data().hosts;
  let offered;
  const address = h("select", { onchange: changed });
  const choose = () => {
    const was = address.value;
    offered = hostChoices(hostList, kept);
    // A kept address the owner no longer gives Articles stays chosen, so saving never moves it unasked.
    fill(address,
      offered.gone ? h("option", { value: addressOf(offered.gone), selected: true }, `${offered.gone} (no longer yours)`)
        : offered.chosen || was ? null : h("option", { value: "" }, "Choose an address"),
      offered.choices.map((c) => h("option", { value: c.value, selected: c.value === (was || offered.chosen) }, c.host)));
  };
  choose();
  // The way to Articles' own settings, where the address, user name and password are given, and back.
  const recheck = async () => {
    try {
      hostList = (await ctx.call("sites.hosts", {})).hosts ?? [];
    } catch (error) {
      return say(why(error), "bad");
    }
    choose();
    show();
    return undefined;
  };
  const settingsWay = () => h("div", { class: "stack" },
    h("div", { class: "row" },
      h("button", { type: "button", class: "btn small", onclick: () => ctx.openSettings().catch((error) => say(why(error), "bad")) }, OPEN_SETTINGS),
      h("button", { type: "button", class: "btn small", onclick: recheck }, "Check again")),
    h("span", { class: "help" }, SETTINGS_KEPT));
  const publisher = h("select", { onchange: () => { show(); changed(); } },
    publishers.map((p) => h("option", { value: p.id, selected: p.id === (site?.publisher ?? "practice") }, p.label)));
  const show = () => {
    help.textContent = PUBLISH_HELP[publisher.value] ?? "";
    fill(wordpress, publisher.value === "wordpress"
      ? [offered.gone ? h("p", { class: "gone", role: "note" }, goneSaid(offered.gone)) : null,
        offered.choices.length || offered.gone
          ? field("WordPress site address", address, "One of your own addresses. You add them, each with its user name and application password, under Settings, Apps, Articles.")
          : h("p", { class: "note", role: "note" }, `${NO_HOSTS} Until then this site cannot be saved as a WordPress site.`),
        offered.choices.length && !offered.gone ? null : settingsWay()] : null);
  };
  show();
  const timezone = zonePicker({ value: v(site?.timezone), ownerZone: ctx.ownerZone, labelId: "site-zone-label", onchange: changed });
  const style = headerStyle(ctx, site);
  const website = websiteFill(ctx, {
    current: () => ({ name: name.value, audience: audience.value, register: register.value, samples: samples.rows(),
      length: length.value, headings: headings.value, closing: closing.value }),
    put: (values) => {
      const inputs = { name, audience, register, length, headings, closing };
      for (const [key, value] of Object.entries(values)) {
        if (key === "samples") samples.set(value);
        else if (inputs[key]) inputs[key].value = value;
      }
      changed();
    },
  });
  const save = async () => {
    // Asked afresh: an address may have been added or removed since the page was drawn.
    let hosts = ctx.data().hosts ?? [];
    if (publisher.value === "wordpress") {
      try {
        hosts = (await ctx.call("sites.hosts", {})).hosts ?? [];
      } catch (error) {
        return say(why(error), "bad");
      }
    }
    const made = siteFromForm({ name: name.value, audience: audience.value, register: register.value, samples: samples.rows(),
      length: length.value, headings: headings.value, closing: closing.value, publisher: publisher.value,
      wordpress_url: offered.choices.length || offered.gone ? address.value : "", timezone: timezone.value() }, site?.id, hosts, kept);
    if (made.error) return say(made.error, "bad");
    try {
      const saved = await ctx.call("sites.save", made.site);
      const words = style.changed();
      if (words !== null) {
        try {
          await ctx.call("header.style.set", { site_id: saved.id, style: words });
        } catch (error) {
          ctx.state.editingSite = saved.id;
          await ctx.refresh();
          say(`Saved ${saved.name}, but not its header image style: ${why(error)}`, "bad");
          return undefined;
        }
      }
      ctx.state.editingSite = null;
      if (made.warning) say(`Saved ${saved.name}, but ${made.warning}`, "warn");
      else say(`Saved ${saved.name}.`, "ok");
      await ctx.refresh();
    } catch (error) {
      say(why(error), "bad");
    }
    return undefined;
  };
  return h("form", { class: "form card", onsubmit: (event) => { event.preventDefault(); save(); } },
    h("h3", {}, site ? `Edit ${site.name}` : "A new site"),
    website.el,
    field("Name", name),
    field("Who it writes for", audience),
    field("How it sounds", register, "A sentence or two, in your own words."),
    samples.el,
    h("fieldset", { class: "field group" }, h("legend", { class: "label" }, "The shape an article usually takes"),
      h("span", { class: "help" }, "In your own words. The writer is given these as guidance; nothing counts words or headings."),
      field("Length", length), field("Headings", headings), field("How it ends", closing)),
    field("Where it publishes", publisher), help, wordpress,
    style.el,
    // Not a <label>: a click on the list inside it would press the button again.
    h("div", { class: "field" }, h("span", { class: "label", id: "site-zone-label" }, "Time zone"), timezone.el,
      h("span", { class: "help" }, "The times on this page for the site's articles are read in it. “Use mine” follows your own time zone.")),
    h("div", { class: "row" }, h("button", { type: "submit", class: "btn primary" }, "Save"),
      h("button", { type: "button", class: "btn", onclick: () => { ctx.state.editingSite = null; ctx.render(); } }, "Cancel")));
}

/**
 * "Fill in from the website": the address is typed here and used only for this; it is not
 * kept on the site. The app reads a few of the site's pages and proposes values, which go
 * into the form's empty fields only. Returns {el}.
 */
function websiteFill(ctx, { current, put }) {
  const address = h("input", { type: "text", inputmode: "url", maxlength: 2000, placeholder: "https://www.example.org", autocomplete: "off",
    onkeydown: (event) => { if (event.key === "Enter") { event.preventDefault(); go(); } } });
  const said = h("p", { class: "small", role: "status", "aria-live": "polite" });
  const button = h("button", { type: "button", class: "btn", onclick: () => go() }, "Fill in from the website");
  const show = (words, tone = "") => { said.className = `small${tone ? ` ${tone}` : " muted"}`; said.textContent = words; };
  async function go() {
    const asked = addressFrom(address.value);
    if (asked.error) return show(asked.error, "problem");
    button.disabled = true;
    button.textContent = "Reading the website…";
    button.setAttribute("aria-busy", "true");
    show("Reading the site's home page and a few of its articles. This can take a minute.");
    try {
      const got = await ctx.call("sites.fill", { address: asked.address });
      const merged = fillEmpty(current(), got);
      put(merged.values);
      show(fillSaid(merged, got.notes ?? []));
    } catch (error) {
      show(why(error), "problem");
    } finally {
      button.disabled = false;
      button.textContent = "Fill in from the website";
      button.removeAttribute("aria-busy");
    }
    return undefined;
  }
  const names = FILL_FIELDS.map(([, words]) => words).join(", ");
  return { el: h("fieldset", { class: "field group" }, h("legend", { class: "label" }, "Start from the website"),
    h("label", { class: "field" }, h("span", { class: "label" }, "Website address"), address,
      h("span", { class: "help" }, `Optional, and not kept. Articles reads the home page and a few recent articles on that site and fills in what is still empty below (${names}). You can change all of it, and nothing is saved until you press Save.`)),
    h("div", { class: "row" }, button), said) };
}

/**
 * How the site's header images should look, in the owner's own words: shown only when
 * header images are switched on. Returns {el, changed()}: the words to save, or null when
 * there is nothing to save (they were not changed, or could not be shown).
 */
function headerStyle(ctx, site) {
  if (!ctx.data().header_images) return { el: null, changed: () => null };
  let loaded = site ? null : "";
  const box = h("textarea", { rows: 3, maxlength: 2000, disabled: Boolean(site), placeholder: site ? "Loading…" : "",
    oninput: () => { ctx.state.typed = true; } });
  const help = h("span", { class: "help" }, "How this site's pictures should look, in your own words. Articles adds no style of its own, "
    + "and makes no header image for a site that has none. A header image never shows a real person.");
  if (site) {
    ctx.call("header.style.get", { site_id: site.id }).then((got) => {
      loaded = String(got?.style ?? "");
      box.value = loaded;
      box.placeholder = "";
      box.disabled = false;
    }).catch((error) => {
      box.placeholder = "";
      help.textContent = `The style guidance could not be shown, so it is left as it is. ${why(error)}`;
    });
  }
  return { el: h("label", { class: "field" }, h("span", { class: "label" }, "Header image style"), box, help),
    changed: () => styleChange(loaded, box.value) };
}

/** Writing samples as rows, with Add and Remove. Returns {el, rows(), set(list)}. */
function sampleRows(stored, changed) {
  const rows = [...stored];
  const list = h("div", { class: "sample-rows" });
  const draw = () => fill(list, rows.length ? rows.map((text, i) => {
    const n = i + 1;
    const box = h("textarea", { rows: 4, value: text, "aria-label": `Sample ${n}`, placeholder: "An article, or a part of one, that sounds right",
      oninput: () => { rows[i] = box.value; } });
    return h("div", { class: "sample-row", role: "group", "aria-label": `Sample ${n}` }, box,
      h("button", { type: "button", class: "btn small", "aria-label": `Remove sample ${n}`, onclick: () => { rows.splice(i, 1); changed(); draw(); } }, "Remove"));
  }) : h("p", { class: "small muted" }, "No samples yet."));
  draw();
  const add = h("button", { type: "button", class: "btn small", onclick: () => {
    rows.push("");
    changed();
    draw();
    list.querySelectorAll("textarea")[rows.length - 1]?.focus();
  } }, "Add a sample");
  return {
    el: h("fieldset", { class: "field group" }, h("legend", { class: "label" }, "Writing samples"),
      h("span", { class: "help" }, "Each teaches the voice; their words and shape are not copied. Up to 20,000 characters in all."),
      list, h("div", { class: "row" }, add)),
    rows: () => [...rows],
    set: (list) => { rows.splice(0, rows.length, ...list); changed(); draw(); },
  };
}
