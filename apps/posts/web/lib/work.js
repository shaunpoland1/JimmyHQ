// Where a piece came from, who worked on it (by role), and the one-line-per-step record.
// Pure: no DOM, no bridge.

const STEP_WORDS = {
  proposed: "Started", reading: "Read the source", overlap_check: "Looked for overlap",
  held_overlap: "Overlap", analysing: "Looked at the pictures", captioning: "Wrote the caption",
  writing: "Wrote the draft", drafted: "Ran the checks", in_check: "Checked the draft",
  revising: "Revised", ready: "Your decision", edit: "Your edit", stalled: "Stopped",
};

// Which part of the work a step is, for "who worked on this".
const JOBS = {
  writing: "wrote", revising: "wrote", captioning: "wrote",
  in_check: "checked", analysing: "looked at the pictures",
};
const JOB_ORDER = ["looked at the pictures", "wrote", "checked"];

/** "From a link you pasted (news.example.com)", from the first line of the record. */
export function cameFrom(turns) {
  const first = (turns ?? []).find((t) => t.step === "proposed");
  const gloss = String(first?.gloss ?? "").trim();
  const said = gloss.replace(/^Started from\s+/i, "").replace(/\.$/, "");
  return said && said !== gloss ? `From ${said}` : gloss || "Where it came from was not recorded";
}

function who(turn) {
  const role = String(turn.role || "");
  const { local: ranHere } = turn; // the record's "local": 1 on the owner's machines, 0 hosted
  const where = ranHere === 1 || ranHere === true ? "on your computer" : ranHere === 0 || ranHere === false ? "hosted" : "";
  const model = String(turn.model || "");
  const more = [model, where].filter(Boolean).join(", ");
  return `the “${role}” role${more ? ` (${more})` : ""}`;
}

/**
 * Who worked on the piece, by role: "Wrote: the “smart” role (hosted). Checked: the
 * “default” role (on your computer). You sent it back once." The code's own steps are
 * not named; the owner's are counted.
 */
export function whoWorked(turns) {
  const jobs = new Map();
  let sentBack = 0;
  let edits = 0;
  for (const turn of turns ?? []) {
    if (turn.role === "owner") {
      if (/^You sent it back/.test(turn.gloss || "")) sentBack += 1;
      if (turn.step === "edit") edits += 1;
      continue;
    }
    const job = JOBS[turn.step];
    if (!job || turn.role === "code" || !turn.role) continue;
    if (!jobs.has(job)) jobs.set(job, new Set());
    jobs.get(job).add(who(turn));
  }
  const parts = JOB_ORDER.filter((j) => jobs.has(j)).map((j) => `${j[0].toUpperCase()}${j.slice(1)}: ${[...jobs.get(j)].join(" and ")}.`);
  if (sentBack) parts.push(`You sent it back ${sentBack === 1 ? "once" : `${sentBack} times`}.`);
  if (edits) parts.push(`You edited it ${edits === 1 ? "once" : `${edits} times`}.`);
  return parts.length ? parts.join(" ") : "No model has worked on it yet.";
}

/** The record, one line a step: {id, when, step, who, gloss}. */
export function workLines(turns) {
  return (turns ?? []).map((t) => ({
    id: t.id,
    when: t.created,
    step: STEP_WORDS[t.step] ?? t.step,
    who: t.role === "owner" ? "You" : t.role === "code" || !t.role ? "Posts" : who(t),
    gloss: String(t.gloss ?? ""),
  }));
}
