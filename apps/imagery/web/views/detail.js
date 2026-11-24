// One picture, large, with its words. JimmyHQ draws it (`hq.files.picture`) no larger than the
// owner's "Longest side of a picture shown in full (pixels)" setting (2048 to begin with). It
// fits the screen, and a tap shows it bigger (at its drawn size, to scroll). The address of
// the picture is revoked when the window closes.
import { dialog, h, why } from "../_ui.js";
import { whenText } from "../lib/format.js";
import { smallerNote } from "../lib/saving.js";
import { lazyImage } from "../lib/thumbs.js";
import { measure, saveDetails, savePicture } from "./saving.js";

function largeView(ctx, row) {
  const box = h("div", { class: "big" });
  const note = h("p", { class: "muted small" });
  let url = null;
  let closed = false;
  let loaded = null;

  function forget() {
    if (url) URL.revokeObjectURL(url);
    url = null;
  }

  function show(blob) {
    forget();
    url = URL.createObjectURL(blob);
    const img = h("img", { src: url, alt: row.prompt || "A picture" });
    const zoom = h("button", {
      type: "button", class: "zoom", "aria-pressed": "false", "aria-label": "Show the picture bigger",
      onclick: () => {
        const on = box.classList.toggle("zoomed");
        zoom.setAttribute("aria-pressed", String(on));
        zoom.setAttribute("aria-label", on ? "Fit the picture to the screen" : "Show the picture bigger");
        hint.textContent = on ? "Tap the picture to fit it to the screen again." : "Tap the picture to see it bigger.";
      },
    }, img);
    const hint = h("p", { class: "muted small" }, "Tap the picture to see it bigger.");
    box.replaceChildren(zoom, hint);
    measure(blob).then((size) => {
      if (!closed && size) note.textContent = smallerNote(row, size.width, size.height);
    });
  }

  function fail(error) {
    box.replaceChildren(
      h("div", { class: "thumb" }, lazyImage(h, row.ref, row.prompt, 320)),
      h("p", { class: "problem small", role: "alert" }, why(error)),
      h("button", { type: "button", class: "btn small", onclick: load }, "Try again"));
  }

  function load() {
    box.replaceChildren(h("p", { class: "muted small", role: "status" }, "Loading the picture…"));
    note.textContent = "";
    loaded = ctx.picture(row.ref);
    loaded.then((blob) => { if (!closed) show(blob); }, (error) => { if (!closed) fail(error); });
  }

  load();
  return {
    box,
    note,
    loaded: () => loaded,
    close() {
      closed = true;
      forget();
    },
  };
}

export function open(ctx, row) {
  const given = row.kind === "given";
  const view = largeView(ctx, row);
  const result = h("p", { class: "small", role: "status" });
  const show = (done) => {
    result.textContent = done.text;
    result.className = `small ${done.ok ? "ok" : "problem"}`;
  };
  const buttons = [];
  const guard = (work) => async () => {
    for (const b of buttons) b.disabled = true;
    try {
      show(await work());
    } finally {
      for (const b of buttons) b.disabled = false;
    }
  };
  buttons.push(
    h("button", { type: "button", class: "btn primary", onclick: guard(() => savePicture(ctx, row, view.loaded())) }, "Save picture"),
    h("button", { type: "button", class: "btn", onclick: guard(() => saveDetails(ctx, row)) }, "Save details"));
  const lines = [
    given ? "A picture you gave Imagery." : `${row.provider}, model ${row.model}, shape ${row.aspect}`,
    whenText(row.created, ctx.zone),
    row.width && row.height ? `${row.width} by ${row.height} pixels` : "",
    row.style ? `Style: ${row.style}` : "",
    row.said ? `The service said: ${row.said}` : "",
  ].filter(Boolean);
  dialog({
    title: given ? "Your picture" : "Picture",
    body: h("div", {},
      view.box,
      view.note,
      h("p", { class: "prompt" }, row.prompt),
      lines.map((line) => h("p", { class: "muted small" }, line)),
      h("div", { class: "row" }, buttons),
      result),
    cancel: "Close",
    actions: [],
  }).then(() => view.close());
}
