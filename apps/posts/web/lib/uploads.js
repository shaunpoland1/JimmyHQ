// A picture upload that JimmyHQ refused, in words for the owner. JimmyHQ takes a
// picture only for a short while after the owner chose it; past that it answers
// `not_allowed` with a sentence written for the app's code. Pure.

/** Whether no later picture of the same pick can be taken either. */
export function pickRanOut(error) {
  return Boolean(error) && error.code === "not_allowed";
}

/**
 * The sentence for a refused upload. `left` is how many of the chosen pictures were not
 * added (this one and those after it). Any other refusal keeps the sentence it came with.
 */
export function uploadProblem(error, left = 1) {
  if (!pickRanOut(error)) return (error && error.message) || "That picture could not be added. Try again.";
  return left > 1
    ? `That took too long, so the last ${left} pictures were not added. Choose them again.`
    : "That took too long, so the last picture was not added. Choose it again.";
}
