// Evidence on an article: which sentence of which source each claim rests on. Pure: no
// DOM, no bridge.
//
// The program (articles/screen.py `evidence`) reads the article as the checks read it and
// sends each sentence with what it cites ([[source, sentence]]), the place of each number
// and quotation in it with the id of the check that judged it, and the ids of the other
// questions about it. Check evidence is written "S2:5" (source 2, sentence 5) or "S2" (the
// source as a whole). This file turns that into what the page lights up: for each source,
// the sentences to show.

/** "S2:5" -> {source: 2, sentence: 5}; "S2" -> {source: 2, sentence: null}; else null. */
export function parseEvidence(mark) {
  const m = /^S(\d{1,3})(?::(\d{1,5}))?$/.exec(String(mark ?? "").trim());
  if (!m) return null;
  return { source: Number(m[1]), sentence: m[2] === undefined ? null : Number(m[2]) };
}

/**
 * What to light: a Map from source number to its sentence numbers, in order, from any mix
 * of [s, n] pairs and "S2:5" / "S2" marks. A source named as a whole has an empty list.
 */
export function lightFor(items) {
  const out = new Map();
  for (const item of items ?? []) {
    const found = Array.isArray(item) ? { source: Number(item[0]), sentence: Number(item[1]) } : parseEvidence(item);
    if (!found || !Number.isInteger(found.source)) continue;
    if (!out.has(found.source)) out.set(found.source, []);
    const list = out.get(found.source);
    if (Number.isInteger(found.sentence) && found.sentence >= 1 && !list.includes(found.sentence)) list.push(found.sentence);
  }
  for (const list of out.values()) list.sort((a, b) => a - b);
  return new Map([...out.entries()].sort((a, b) => a[0] - b[0]));
}

/** "S1:4, S2:2 and 5": the lit sentences in words, for a label or the status line. */
export function describeLight(lit) {
  const parts = [];
  for (const [source, sentences] of lit) {
    parts.push(sentences.length ? `S${source}:${sentences.join(", ")}` : `S${source}`);
  }
  return parts.join("; ");
}

/** What a check result says about one number or quotation, in one word the page styles by. */
export function standing(result) {
  if (!result) return "unchecked";
  if (result.level === "ask") return result.answer ? "answered" : "not_found";
  if (result.level === "warn") return "warn";
  return "found";
}

export const STANDING_WORDS = {
  found: "Found in a sentence it cites",
  warn: "Not word for word in any source",
  not_found: "Not found where it should be: a question for you",
  answered: "Not found; you answered",
  unchecked: "Not checked on this version",
};

/** Check results by id. */
export function byId(checks) {
  return new Map((checks ?? []).map((c) => [c.id, c]));
}

/**
 * One sentence cut into plain runs and marked runs. A marked run carries its kind, its
 * check, its standing and what it lights. Joined back together the runs are the sentence.
 */
export function segments(sentence, checks) {
  const text = String(sentence?.text ?? "");
  const out = [];
  let at = 0;
  for (const mark of [...(sentence?.marks ?? [])].sort((a, b) => a.start - b.start)) {
    if (mark.start < at || mark.end > text.length || mark.end <= mark.start) continue;
    if (mark.start > at) out.push({ kind: "text", text: text.slice(at, mark.start) });
    const check = mark.check ? checks.get(mark.check) ?? null : null;
    out.push({ kind: mark.kind, text: text.slice(mark.start, mark.end), check, standing: standing(check),
      light: lightFor(check?.evidence ?? []) });
    at = mark.end;
  }
  if (at < text.length) out.push({ kind: "text", text: text.slice(at) });
  return out;
}

/** The questions and warnings about one sentence, from its marks and its own checks. */
export function sentenceChecks(sentence, checks) {
  const ids = [...(sentence?.checks ?? []), ...(sentence?.marks ?? []).map((m) => m.check).filter(Boolean)];
  return [...new Set(ids)].map((id) => checks.get(id)).filter((c) => c && c.level !== "ok");
}

/**
 * How a sentence stands: "ask" when a question about it is open, "answered" when every
 * question was answered, "warn" for a warning, "cited" when it cites a sentence, else
 * "plain".
 */
export function sentenceStanding(sentence, checks) {
  const raisedHere = sentenceChecks(sentence, checks);
  if (raisedHere.some((c) => c.level === "ask" && !c.answer)) return "ask";
  if (raisedHere.some((c) => c.level === "ask")) return "answered";
  if (raisedHere.some((c) => c.level === "warn")) return "warn";
  return (sentence?.cites ?? []).length ? "cited" : "plain";
}

/**
 * What a press on a sentence, a mark or a check lights, and the question to point at when
 * nothing can be lit: {light, check}. A mark lights its check's evidence, and with none
 * points at its question; a sentence lights what it cites, and when it cites nothing
 * points at the first question about it.
 */
export function target(what, checks) {
  if (what?.kind === "number" || what?.kind === "quote") {
    if (what.light?.size) return { light: what.light, check: what.check };
    return { light: new Map(), check: what.check };
  }
  if (what?.level) return { light: lightFor(what.evidence ?? []), check: what };
  const light = lightFor(what?.cites ?? []);
  const open = sentenceChecks(what, checks)[0] ?? null;
  return { light, check: light.size ? null : open };
}

/** The source sentences cited anywhere in the article, lit faintly before any press. */
export function citedEverywhere(blocks) {
  const pairs = [];
  for (const block of blocks ?? []) for (const s of block.sentences ?? []) pairs.push(...(s.cites ?? []));
  return lightFor(pairs);
}

/** "2 questions open, 1 warning." in the words of articles/checked.py `summary`. */
export function checkSummary(checks) {
  const asks = (checks ?? []).filter((c) => c.level === "ask" && !c.answer).length;
  const warns = (checks ?? []).filter((c) => c.level === "warn").length;
  const parts = [];
  if (asks) parts.push(`${asks} question${asks === 1 ? "" : "s"} open`);
  if (warns) parts.push(`${warns} warning${warns === 1 ? "" : "s"}`);
  if (!parts.length) return "Every check passed.";
  const said = parts.join(", ");
  return `${said[0].toUpperCase()}${said.slice(1)}.`;
}

/** The checks to show, questions first (open before answered), then warnings. */
export function raised(checks) {
  const rank = (c) => (c.level === "ask" ? (c.answer ? 1 : 0) : 2);
  return (checks ?? []).filter((c) => c.level !== "ok").sort((a, b) => rank(a) - rank(b) || a.id - b.id);
}
