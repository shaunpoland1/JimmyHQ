// The new-topic form's Name field: filling it from a suggestion chip. Pure: no DOM.

/** Whether a chip may replace what is in the Name field: only when it is empty, or still holds
 * exactly the text a chip put there (`filled`, null if none did). Text the owner typed or edited
 * is never replaced. */
export function mayFill(current, filled) {
  const now = String(current ?? "").trim();
  return now === "" || (filled != null && now === String(filled).trim());
}

/** What the Name field and the remembered chip text become when `chip` is clicked. */
export function fillName(current, filled, chip) {
  const text = String(chip ?? "").replace(/\s+/g, " ").trim();
  if (!text || !mayFill(current, filled)) return { name: String(current ?? ""), filled, changed: false };
  return { name: text, filled: text, changed: true };
}

/** The chips to show: plain, non-empty, each once ignoring case, none the owner has as a topic. */
export function chipNames(suggested, existing = []) {
  const fold = (t) => String(t).replace(/\s+/g, " ").trim().toLowerCase();
  const seen = new Set(existing.map(fold));
  const out = [];
  for (const raw of Array.isArray(suggested) ? suggested : []) {
    const text = String(raw ?? "").replace(/\s+/g, " ").trim();
    if (!text || seen.has(fold(text))) continue;
    seen.add(fold(text));
    out.push(text);
  }
  return out;
}
