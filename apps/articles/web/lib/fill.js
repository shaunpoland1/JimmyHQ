// Filling a site's form from its website: what the app proposes is put in the form's empty
// fields only, never over what the owner typed. Pure; the page does the typing.

/** The form's fields in the order they are told, with the words the page uses for each. */
export const FILL_FIELDS = [
  ["name", "name"],
  ["audience", "who it writes for"],
  ["register", "how it sounds"],
  ["samples", "writing samples"],
  ["length", "length"],
  ["headings", "headings"],
  ["closing", "how it ends"],
];

export const NO_ADDRESS = "Type the website's address first, such as https://www.example.org. Nothing was read.";

/** The address to send, or an error sentence. A bare host is read as https. */
export function addressFrom(text) {
  const typed = String(text ?? "").trim();
  if (!typed) return { error: NO_ADDRESS };
  if (/\s/.test(typed)) return { error: "A web address has no spaces. Nothing was read." };
  const scheme = /^([a-z][a-z0-9+.-]*):\/\//i.exec(typed);
  if (scheme && !/^https?$/i.test(scheme[1])) return { error: "Articles reads only web addresses that start with https or http. Nothing was read." };
  return { address: scheme ? typed : `https://${typed}` };
}

function isEmpty(value) {
  return Array.isArray(value) ? value.every((v) => !String(v ?? "").trim()) : !String(value ?? "").trim();
}

/**
 * What to put in the form. `current` has the form's fields as typed (samples as rows);
 * `proposal` is the app's answer ({fields, samples}). Returns
 * {values, filled, kept, missing}: `values` holds only what is to be put in (a field the
 * owner already typed in is never in it), and the three lists name each field once:
 * filled in, left alone because it was typed, and not shown by the website.
 */
export function fillEmpty(current, proposal) {
  const proposed = { ...(proposal?.fields ?? {}), samples: proposal?.samples ?? [] };
  const values = {};
  const filled = [];
  const kept = [];
  const missing = [];
  for (const [key] of FILL_FIELDS) {
    const offered = proposed[key];
    const has = !isEmpty(offered);
    if (!isEmpty(current?.[key])) kept.push(key);
    else if (has) {
      values[key] = key === "samples" ? [...offered] : String(offered);
      filled.push(key);
    } else missing.push(key);
  }
  return { values, filled, kept, missing };
}

function words(keys) {
  const named = keys.map((k) => FILL_FIELDS.find(([key]) => key === k)?.[1] ?? k);
  return named.length > 1 ? `${named.slice(0, -1).join(", ")} and ${named.at(-1)}` : named[0];
}

/** The one plain line under the button: what was filled in and what was left alone. */
export function fillSaid(result, notes = []) {
  const parts = [];
  parts.push(result.filled.length ? `Filled in: ${words(result.filled)}.` : "Nothing was filled in.");
  if (result.kept.length) parts.push(`Left alone because you had typed them: ${words(result.kept)}.`);
  if (result.missing.length) parts.push(`The website did not show: ${words(result.missing)}.`);
  parts.push("Nothing is saved until you press Save.");
  return [...parts, ...notes].join(" ");
}
