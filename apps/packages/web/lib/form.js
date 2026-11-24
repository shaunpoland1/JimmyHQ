// The Add a package form: cleaning what was typed, and the carrier picker. Pure: no DOM.

export const DETECT = "";
export const OTHER = "__other";

/** A tracking number with its spaces taken out. */
export function cleanNumber(text) {
  return String(text ?? "").replace(/\s+/g, "");
}

/** The sentence that says what is missing, or "" when the form can be sent. */
export function addProblem({ label, number }) {
  if (!String(label ?? "").trim()) return "Give the package a short name, such as \"Garden hose\", so you can tell it apart.";
  if (!cleanNumber(number)) return "Type the tracking number.";
  return "";
}

/** The picker's choices: detect it, the carriers the owner saved, then another code. */
export function carrierOptions(carriers, tracker) {
  const out = [{ value: DETECT, label: tracker?.registers ? "Detect it" : "Any (none chosen)" }];
  for (const c of carriers ?? []) out.push({ value: c.code, label: `${c.name} (${c.code})` });
  out.push({ value: OTHER, label: "Another carrier code…" });
  return out;
}

/** What to send for the picker's value: {carrier, carrier_name}. Typing a name saves the code under it. */
export function carrierFrom(choice, code, name) {
  if (choice !== OTHER) return { carrier: choice || "", carrier_name: "" };
  return { carrier: String(code ?? "").replace(/\s+/g, ""), carrier_name: String(name ?? "").trim() };
}

/** The picker's value that shows a package's carrier now. */
export function choiceOf(row, carriers) {
  if (!row.carrier) return DETECT;
  return (carriers ?? []).some((c) => c.code === row.carrier) ? row.carrier : OTHER;
}
