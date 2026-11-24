// The one safe renderer for an article's markup. Pure: no DOM, no bridge.
//
// An article is HTML limited to the allowed set (articles/markup.py `ALLOWED`), with
// citation marks such as [S2:5] in its words. The page never hands that text to the
// browser as HTML. `parse` reads it into a small tree of plain objects, keeping only the
// allowed tags, no attribute but a link to a source by number, and every word as text;
// `build` (in views) turns the tree into elements with the page helper, whose every piece
// of text is a text node. Anything else (a script, a style, an image, an event handler, a
// link anywhere but a source) never becomes an element, and what was taken out is said.

export const ALLOWED = new Set(["p", "h2", "h3", "ul", "ol", "li", "strong", "em", "blockquote", "a", "br"]);
const VOID = new Set(["br"]);
// Tags whose content is dropped with them, as the sanitiser drops it.
const DROP_CONTENT = new Set(["script", "style", "iframe", "object", "embed", "noscript", "template",
  "svg", "math", "head", "title", "textarea", "select"]);
const SOURCE_HREF = /^S(\d{1,3})$/;
// The same pattern as articles/markup.py `MARK`.
const MARK = /\[(S\d{1,3}:\s*\d{1,5}(?:\s*[-–,;]\s*(?:S\d{1,3}:\s*)?\d{1,5})*)\]/g;
const PART = /S(\d{1,3}):\s*([\d\s,\-–]+)/g;
const RANGE_MOST = 20; // as articles/markup.py: a wider range cites only its ends
const TOKEN = /<!--[\s\S]*?(?:-->|$)|<\/?([A-Za-z][A-Za-z0-9-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|[^<]+|</g;
const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

/** Entities as the browser would read them, for the few an article can hold. Never markup. */
export function decode(text) {
  return String(text).replace(/&(#x[0-9a-f]{1,6}|#\d{1,7}|[a-z]{2,6});/gi, (whole, name) => {
    if (name[0] === "#") {
      const code = name[1] === "x" || name[1] === "X" ? parseInt(name.slice(2), 16) : parseInt(name.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff)
        ? String.fromCodePoint(code) : "�";
    }
    return NAMED[name.toLowerCase()] ?? whole;
  });
}

/** The (source, sentence) pairs one mark cites, as articles/markup.py `parse_mark` reads them. */
export function parseMark(inner) {
  const out = [];
  const seen = new Set();
  const add = (s, n) => {
    const key = `${s}:${n}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push([s, n]);
    }
  };
  for (const [, source, numbers] of String(inner).matchAll(PART)) {
    for (const part of numbers.trim().replace(/^[ ,]+|[ ,]+$/g, "").split(/\s*,\s*/)) {
      if (!part) continue;
      const ends = part.split(/\s*[-–]\s*/).filter((x) => /^\d+$/.test(x.trim())).map(Number);
      if (ends.length === 2 && ends[0] <= ends[1] && ends[1] - ends[0] < RANGE_MOST) {
        for (let n = ends[0]; n <= ends[1]; n += 1) add(Number(source), n);
      } else {
        for (const n of ends) add(Number(source), n);
      }
    }
  }
  return out;
}

/** Text cut into words and citation marks: [{text}] and [{cite: [[s, n]], text: "[S2:5]"}]. */
export function splitMarks(text) {
  const out = [];
  let at = 0;
  for (const m of String(text).matchAll(MARK)) {
    if (m.index > at) out.push({ text: text.slice(at, m.index) });
    out.push({ cite: parseMark(m[1]), text: m[0] });
    at = m.index + m[0].length;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

function hrefOf(attrs) {
  const m = /\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))/i.exec(attrs ?? "");
  return m ? decode(m[1] ?? m[2] ?? m[3] ?? "").trim() : null;
}

/**
 * The article's markup as a tree: {nodes, removed}. A node is {tag, children} for an
 * allowed tag ({tag: "a", source: 2, children} for a link to source 2), {text} for words,
 * or {cite, text} for a citation mark. `sources` is the set of source numbers the article
 * has, or null to keep any link to "S<n>". `removed` says what was left out, in words.
 */
export function parse(markup, sources = null) {
  const root = { tag: "root", children: [] };
  const stack = [root];
  const removed = [];
  const note = (what) => { if (!removed.includes(what)) removed.push(what); };
  let dropping = 0;
  const text = String(markup ?? "");
  for (const m of text.matchAll(TOKEN)) {
    const token = m[0];
    if (token.startsWith("<!--")) {
      note("comments");
      continue;
    }
    const name = m[1]?.toLowerCase();
    if (!name) {
      if (!dropping) addText(stack[stack.length - 1], decode(token));
      continue;
    }
    const closing = token.startsWith("</");
    if (DROP_CONTENT.has(name)) {
      if (closing) dropping = Math.max(0, dropping - 1);
      else if (!/\/\s*>$/.test(token)) {
        dropping += 1;
        note(`a <${name}> and what was in it`);
      } else note(`a <${name}>`);
      continue;
    }
    if (dropping) continue;
    if (!ALLOWED.has(name)) {
      note(`<${name}> tags (their words were kept)`);
      continue;
    }
    if (closing) {
      close(stack, name);
      continue;
    }
    const attrs = m[2] ?? "";
    for (const [, attr] of attrs.matchAll(/([^\s=/"']+)\s*(?:=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?/g)) {
      if (!(name === "a" && attr.toLowerCase() === "href")) note(`the "${attr.toLowerCase()}" attribute`);
    }
    if (VOID.has(name)) {
      stack[stack.length - 1].children.push({ tag: name, children: [] });
      continue;
    }
    if (name === "a") {
      const href = hrefOf(attrs) ?? "";
      const found = SOURCE_HREF.exec(href);
      const n = found ? Number(found[1]) : null;
      if (n === null || (sources && !sources.has(n))) {
        note(`a link to ${href.slice(0, 80) || "nowhere"}, which is not one of the sources (its words were kept)`);
        const unwrapped = { tag: "unwrapped", children: [] };
        stack[stack.length - 1].children.push(unwrapped);
        stack.push(unwrapped);
        continue;
      }
      const link = { tag: "a", source: n, children: [] };
      stack[stack.length - 1].children.push(link);
      stack.push(link);
      continue;
    }
    if (/\/\s*>$/.test(token)) continue; // <p/> opens and closes nothing
    const node = { tag: name, children: [] };
    stack[stack.length - 1].children.push(node);
    stack.push(node);
  }
  return { nodes: flatten(root.children), removed };
}

function close(stack, name) {
  const wanted = name === "a" && !stack.some((n) => n.tag === "a") ? "unwrapped" : name;
  if (!stack.some((n, i) => i > 0 && n.tag === wanted)) return; // a stray closing tag
  while (stack.length > 1) {
    const last = stack.pop();
    if (last.tag === wanted) break;
  }
}

function addText(parent, words) {
  if (!words) return;
  for (const piece of splitMarks(words)) parent.children.push(piece);
}

/** A link that was taken off leaves its words where it stood. */
function flatten(nodes) {
  const out = [];
  for (const node of nodes) {
    if (node.tag === "unwrapped") out.push(...flatten(node.children));
    else if (node.children) out.push({ ...node, children: flatten(node.children) });
    else out.push(node);
  }
  return out;
}

/** The words of a tree, as a reader would see them without the marks. */
export function plainText(nodes) {
  let out = "";
  for (const node of nodes ?? []) {
    if (node.cite) continue;
    if (node.text !== undefined) out += node.text;
    else if (node.tag === "br") out += "\n";
    else {
      const inner = plainText(node.children);
      out += ["p", "h2", "h3", "li", "blockquote"].includes(node.tag) ? `${inner}\n` : inner;
    }
  }
  return out;
}

/** Every tag name in a tree, for a test to see nothing else got through. */
export function tagsOf(nodes, into = new Set()) {
  for (const node of nodes ?? []) {
    if (node.tag) {
      into.add(node.tag);
      tagsOf(node.children, into);
    }
  }
  return into;
}

/**
 * The tree as elements, through `h` (the page helper: text nodes only). A link to a source
 * becomes its words and a button naming the source; a citation mark becomes a button
 * that lights the sentences it cites. Nothing is ever a real link or loads anything.
 */
export function build(nodes, h, { onCite = null, onSource = null } = {}) {
  return (nodes ?? []).map((node) => {
    if (node.cite) {
      const label = node.text.slice(1, -1);
      return onCite ? h("button", { type: "button", class: "chip cite", "aria-label": `Show ${label}`, onclick: () => onCite(node.cite) }, label)
        : h("span", { class: "chip cite" }, label);
    }
    if (node.text !== undefined) return node.text;
    if (node.tag === "br") return h("br", {});
    const inner = build(node.children, h, { onCite, onSource });
    if (node.tag === "a") {
      return h("span", { class: "source-link" }, inner, " ",
        onSource ? h("button", { type: "button", class: "chip cite", onclick: () => onSource(node.source) }, `S${node.source}`)
          : h("span", { class: "chip cite" }, `S${node.source}`));
    }
    return h(node.tag, {}, inner);
  });
}
