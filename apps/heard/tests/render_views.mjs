// Draws the page's four views from what the program really answered (JSON on standard
// input), with no browser, and prints what was drawn: its text, its tags, its marks and
// the buttons by their words. Nothing is fetched; `call` answers from the answers given.
import { registerHooks } from "node:module";
import { readFileSync } from "node:fs";

const SUPPLIED = {
  "_ui.js": new URL("../../../sdk/js/ui.js", import.meta.url).href,
  "_bridge.js": new URL("./stub_bridge.js", import.meta.url).href,
};
registerHooks({
  resolve(specifier, context, nextResolve) {
    const name = specifier.split("/").pop() ?? "";
    if (SUPPLIED[name] && (specifier.startsWith("./") || specifier.startsWith("../"))) {
      return { url: SUPPLIED[name], shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

function element(tag) {
  const node = {
    kind: "element", tag, children: [], attrs: {}, style: {}, className: "", value: "", checked: false,
    disabled: false, selected: false, open: false, hidden: false, listeners: {},
    setAttribute(name, value) { node.attrs[name] = value; },
    appendChild(child) { node.children.push(child); return child; },
    replaceChildren(...kids) { node.children = kids; },
    addEventListener(name, fn) { node.listeners[name] = fn; },
    querySelector() { return null; },
    focus() {},
    set textContent(text) { node.children = [{ kind: "text", text, children: [] }]; },
    get textContent() { return node.children.map((c) => c.text ?? c.textContent ?? "").join(""); },
  };
  return node;
}
globalThis.document = { createElement: element, createTextNode: (text) => ({ kind: "text", text: String(text), children: [] }), getElementById: () => null };

const { h } = await import("../../../sdk/js/ui.js");
const days = await import("../web/views/days.js");
const search = await import("../web/views/search.js");
const conversation = await import("../web/views/conversation.js");
const settings = await import("../web/views/settings.js");

const { answers, overview, open } = JSON.parse(readFileSync(0, "utf8"));
const opened = [];
const ctx = {
  state: { overview, open: open ?? null, query: "garden", found: null, tab: "days" },
  zone: "UTC",
  call: async (name) => {
    if (!(name in answers)) throw new Error(`The page asked for ${name}, which the test did not answer.`);
    return answers[name];
  },
  save: async () => ({}),
  openSettings: async () => ({}),
  show: async () => {},
  go: async (tab) => { opened.push(`tab:${tab}`); },
  openConversation: async (id, line) => { opened.push(`open:${id}:${line}`); },
  closeConversation: async () => { opened.push("back"); },
  reloadOverview: async () => {},
};

function walk(node, visit) {
  visit(node);
  for (const child of node.children ?? []) walk(child, visit);
}
function summary(root) {
  const out = { text: [], tags: new Set(), buttons: [], marks: [], classes: {} };
  walk(root, (n) => {
    if (n.kind === "text") out.text.push(n.text);
    if (n.tag) out.tags.add(n.tag);
    if (n.tag === "button") out.buttons.push({ text: n.textContent, disabled: !!n.disabled, click: !!n.listeners.click });
    if (n.tag === "mark") out.marks.push(n.textContent);
    for (const c of String(n.className || "").split(" ").filter(Boolean)) out.classes[c] = (out.classes[c] ?? 0) + 1;
  });
  return { text: out.text.join(" "), tags: [...out.tags], buttons: out.buttons, marks: out.marks, classes: out.classes };
}

const drawn = {};
for (const [name, view] of [["days", days], ["search", search], ["conversation", conversation], ["settings", settings]]) {
  if (name === "conversation" && !ctx.state.open) continue;
  const root = h("div", {});
  await view.show(ctx, root, () => true);
  drawn[name] = summary(root);
}
process.stdout.write(JSON.stringify({ drawn, opened }));
