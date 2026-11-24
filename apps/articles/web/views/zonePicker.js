// A time zone picker of the page's own, with the feel of the shell's: a button showing
// the choice and its offset, which opens a list to filter by typing a city, a region or
// an offset. "Use mine" comes first and stores nothing, so the page follows the owner.

import { browserZones, formatOffset, offsetMinutes, shownChoices, startIndex, stepIndex, useMineLabel, zoneName, zoneNames, zoneOptions } from "../lib/zones.js";
import { fill, h } from "../_ui.js";

let made = 0;

/** Returns {el, value()}: `value()` is the IANA name chosen, or "" for the owner's own. */
export function zonePicker({ value = "", ownerZone = "", labelId, onchange = () => {} }) {
  const id = `zone-${(made += 1)}`;
  let chosen = value || "";
  let open = false;
  let options = [];
  let shown = [];
  let active = 0;

  const words = h("span", { class: "grow truncate" });
  const offset = h("span", { class: "small muted mono" });
  const trigger = h("button", {
    type: "button", class: "picker-button", id, "aria-haspopup": "listbox", "aria-expanded": "false",
    "aria-labelledby": labelId ? `${labelId} ${id}` : undefined,
    onclick: () => (open ? hide() : show()),
  }, words, offset, h("span", { class: "muted", "aria-hidden": "true" }, "▾"));
  const search = h("input", {
    type: "text", role: "combobox", "aria-expanded": "true", "aria-controls": `${id}-list`,
    "aria-autocomplete": "list", "aria-label": "Find a time zone by city, region or offset",
    placeholder: "Type a city, a region or an offset such as +2", autocomplete: "off", spellcheck: "false",
    oninput: () => { active = 0; draw(); },
    onkeydown: (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        hide();
      } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        active = stepIndex(active, shown.length, event.key === "ArrowDown" ? 1 : -1);
        draw();
      } else if (event.key === "Enter") {
        event.preventDefault(); // inside the page form: Enter picks, it does not save
        if (shown[active]) choose(shown[active].zone);
      }
    },
  });
  const list = h("div", { id: `${id}-list`, role: "listbox", "aria-label": "Time zones", class: "picker-list" });
  const panel = h("div", { class: "picker-panel", hidden: true }, h("div", { class: "picker-search" }, search), list);

  function label() {
    words.textContent = chosen ? zoneName(chosen) : useMineLabel(ownerZone);
    const minutes = chosen ? offsetMinutes(chosen, new Date()) : null;
    offset.textContent = minutes === null ? "" : formatOffset(minutes);
  }

  function draw() {
    shown = shownChoices(options, search.value, ownerZone);
    if (active >= shown.length) active = 0;
    fill(list, shown.length ? shown.map((option, index) => h("div", {
      id: `${id}-option-${index}`, role: "option", "aria-selected": String(option.zone === chosen),
      class: `picker-option${index === active ? " on" : ""}`, "data-index": String(index),
      onmousedown: (event) => event.preventDefault(),
      onmouseenter: () => { if (active !== index) { active = index; draw(); } },
      onclick: () => choose(option.zone),
    }, h("span", { class: "tick", "aria-hidden": "true" }, option.zone === chosen ? "✓" : ""),
    h("span", { class: "grow truncate" }, option.name),
    option.offset ? h("span", { class: "small muted mono" }, option.offset) : null))
      : h("p", { class: "small muted pad-s" }, "No time zone matches that. Try a nearby large city."));
    if (shown.length) search.setAttribute("aria-activedescendant", `${id}-option-${active}`);
    else search.removeAttribute("aria-activedescendant");
    list.querySelector?.(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }

  function show() {
    // Offsets are worked out when the list opens, so they are right for today.
    options = zoneOptions(zoneNames([chosen, ownerZone], browserZones()), new Date());
    search.value = "";
    shown = shownChoices(options, "", ownerZone);
    active = startIndex(shown, chosen);
    open = true;
    panel.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    draw();
    search.focus();
  }

  function hide() {
    open = false;
    panel.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    trigger.focus();
  }

  function choose(zone) {
    if (zone !== chosen) onchange(zone);
    chosen = zone;
    label();
    hide();
  }

  label();
  return { el: h("div", { class: "picker" }, trigger, panel), value: () => chosen };
}
