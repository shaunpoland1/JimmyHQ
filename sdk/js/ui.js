// The page helper JimmyHQ supplies beside every app's files as `./_ui.js` (sdk/js/ui.js).
// Building the page. Every piece of text, from the owner, an article or a model, goes in
// as a text node: nothing here ever reads text as HTML. Dialogs are the page's own,
// never the browser's.

const ATTRIBUTES = new Set([
  "id", "for", "name", "type", "placeholder", "role", "title", "accept", "multiple",
  "maxlength", "min", "max", "step", "rows", "required", "autocomplete", "tabindex", "alt",
  "lang", "spellcheck", "inputmode", "open", "colspan", "datetime", "list", "hidden", "value",
]);
const PROPERTIES = new Set(["value", "checked", "disabled", "selected", "open"]);

function allowed(name, value) {
  if (name.startsWith("aria-") || name.startsWith("data-")) return true;
  if (name === "src") return /^blob:/.test(String(value)); // a picture handed back by the bridge
  return ATTRIBUTES.has(name);
}

function append(el, children, doc) {
  for (const child of children) {
    if (child === null || child === undefined || child === false || child === "") continue;
    if (Array.isArray(child)) append(el, child, doc);
    else if (typeof child === "string" || typeof child === "number") el.appendChild(doc.createTextNode(String(child)));
    else el.appendChild(child);
  }
}

/**
 * h("button", {class: "btn", onclick: fn}, "Approve"): an element with its properties
 * and children. Strings become text nodes. An attribute not on the short list, an
 * `on…` that is not a function, and a `src` that is not a blob are left out.
 */
export function h(tag, props, ...children) {
  const doc = h.document ?? globalThis.document;
  const el = doc.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = String(value);
    else if (key === "text") el.textContent = String(value);
    else if (key.startsWith("on")) {
      if (typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (PROPERTIES.has(key) && key in el) el[key] = value;
    else if (allowed(key, value)) el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children, doc);
  return el;
}

/** Replace what `el` holds. */
export function fill(el, ...children) {
  el.replaceChildren();
  append(el, children, h.document ?? globalThis.document);
  return el;
}

// --- the status line --------------------------------------------------------------------

let statusTimer = null;

/** One sentence in the status line at the top: what happened, or why nothing did. */
export function say(text, tone = "") {
  const line = document.getElementById("status");
  if (!line) return;
  line.textContent = String(text ?? "");
  line.className = `status ${tone}`.trim();
  clearTimeout(statusTimer);
  if (text && tone !== "bad") statusTimer = setTimeout(() => { line.textContent = ""; line.className = "status"; }, 8000);
}

/**
 * The sentence to show the owner for a refusal: the one JimmyHQ wrote for them when it
 * sent one (`owner_message`), else the refusal's own, else a plain one.
 */
export function why(error) {
  const forOwner = error && typeof error.owner_message === "string" ? error.owner_message : "";
  return forOwner || (error && error.message) || "That did not work, so nothing was changed. Try again.";
}

// --- dialogs ----------------------------------------------------------------------------

let open = null;

export function dialogOpen() {
  return open !== null;
}

/** Close the open dialog, as Cancel would. */
export function closeDialog() {
  open?.close();
}

/**
 * A dialog of the page's own. `actions` are buttons: {label, kind ("primary" or
 * "danger"), run}. `run` may return a sentence to show and stay open (a refusal), or
 * throw an Error whose message is shown; anything else closes the dialog. Cancel and
 * Esc close it with nothing done. Resolves with the label of the button that closed it,
 * or null. The keyboard starts on a field, else the primary button, else Cancel.
 */
export function dialog({ title, intro = "", body = null, actions = [], cancel = "Cancel" }) {
  closeDialog();
  return new Promise((resolve) => {
    const before = document.activeElement;
    const problem = h("p", { class: "problem", role: "alert" });
    const heading = h("h2", { id: "dialog-title" }, title);
    const buttons = actions.map((action) => h("button", {
      type: "button", class: `btn ${action.kind || ""}`.trim(),
      onclick: async () => {
        problem.textContent = "";
        for (const b of buttons) b.disabled = true;
        try {
          const said = await action.run?.();
          if (typeof said === "string" && said) problem.textContent = said;
          else return done(action.label);
        } catch (error) {
          problem.textContent = why(error);
        }
        for (const b of buttons) b.disabled = false;
      },
    }, action.label));
    const wayOut = h("button", { type: "button", class: "btn", onclick: () => done(null) }, cancel);
    const box = h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "dialog-title" },
      heading, intro ? h("p", { class: "muted" }, intro) : null, body, problem,
      h("div", { class: "row end" }, wayOut, buttons));
    const scrim = h("div", { class: "scrim", onclick: (event) => { if (event.target === scrim) done(null); } }, box);
    box.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        done(null);
      }
      if (event.key === "Tab") keepFocus(box, event);
    });
    function done(label) {
      scrim.remove();
      open = null;
      if (before && typeof before.focus === "function") before.focus();
      resolve(label);
    }
    open = { close: () => done(null) };
    document.body.appendChild(scrim);
    startsOn(box.querySelector("textarea, input, select"), buttons, wayOut)?.focus();
  });
}

/**
 * Where the keyboard starts in a dialog: a field to fill in, else the primary button,
 * else the way out. Never a danger button, so Enter or Space cannot delete at once.
 */
export function startsOn(field, buttons, wayOut) {
  return field || buttons.find((b) => b.classList.contains("primary")) || wayOut;
}

function keepFocus(box, event) {
  const all = [...box.querySelectorAll("button, input, select, textarea")].filter((el) => !el.disabled);
  if (!all.length) return;
  const first = all[0];
  const last = all[all.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/** A yes-or-no question in a dialog of the page's own. Resolves true for yes. */
export async function ask(title, intro, yes, kind = "primary") {
  return (await dialog({ title, intro, actions: [{ label: yes, kind }] })) === yes;
}

/** A labelled field: h("label") around its words and the control. */
export function field(words, control, help = "") {
  return h("label", { class: "field" }, h("span", { class: "label" }, words), control, help ? h("span", { class: "help" }, help) : null);
}

// --- dictation --------------------------------------------------------------------------

/**
 * Words put into a text at the cursor (or over the selection), followed by a space, with
 * a space before them when the text before the cursor does not end in one. A cursor that
 * is not a number goes to the end. Returns the new text and where the cursor goes.
 */
export function insertWords(text, start, end, words) {
  const value = String(text ?? "");
  const said = String(words ?? "").trim();
  const at = (n, fallback) => (Number.isInteger(n) && n >= 0 && n <= value.length ? n : fallback);
  const from = at(start, value.length);
  const to = Math.max(from, at(end, from));
  if (!said) return { value, caret: to };
  const before = value.slice(0, from);
  const lead = before && !/\s$/.test(before) ? " " : "";
  const put = `${lead}${said} `;
  return { value: before + put + value.slice(to), caret: from + put.length };
}

/**
 * A Dictate button for a text box. JimmyHQ records on its own surface and hands back only
 * the words, which go in at the cursor; the box then hears an "input" event, as if they
 * had been typed. Needs the "speech.dictate" permission. When dictation is not available
 * the button is disabled and says why, in its title and in a line beside it. `hq` is the
 * page's bridge.
 */
export function dictateButton(hq, box, { hint = "", label = "Dictate" } = {}) {
  const help = h("span", { class: "help dictate-why", hidden: true });
  const button = h("button", { type: "button", class: "btn small", title: "Speak instead of typing" }, label);
  const show = (answer) => {
    const off = !answer || answer.available !== true;
    const reason = off ? String((answer && answer.reason) || "Dictation is not available.") : "";
    button.disabled = off;
    button.title = off ? reason : "Speak instead of typing";
    help.textContent = reason;
    help.hidden = !off;
  };
  const check = () => hq.speech.available().then(show, (error) => show({ available: false, reason: why(error) }));
  button.addEventListener("click", async () => {
    const start = box.selectionStart;
    const end = box.selectionEnd;
    button.disabled = true;
    try {
      const { text } = await hq.speech.dictate(hint ? { hint } : {});
      const next = insertWords(box.value, start, end, text);
      if (next.value !== box.value) {
        box.value = next.value;
        box.dispatchEvent(new Event("input", { bubbles: true }));
      }
      box.focus();
      box.setSelectionRange?.(next.caret, next.caret);
    } catch (error) {
      if (!error || error.code !== "cancelled") say(why(error), "bad");
    }
    button.disabled = false;
    check();
  });
  check();
  return h("span", { class: "dictate" }, button, help);
}
