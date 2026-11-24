// Comparing what is in the editor with another version of the same note, line
// by line, so the owner can see what a reload would change before choosing.

export type DiffLine = { type: "added" | "removed"; /** 1-based line in the version it belongs to. */ line: number; text: string };

export type TextDiff = {
  same: boolean;
  /** Lines the other version has that the editor does not. */
  added: number;
  /** Lines the editor has that the other version does not. */
  removed: number;
  /** The first changed lines, in order, for a short preview. */
  lines: DiffLine[];
  /** True when there were more changed lines than `lines` holds. */
  more: boolean;
};

const MAX_CELLS = 250_000;
export const PREVIEW_LINES = 12;

function split(text: string): string[] {
  return text.replace(/\r\n?/g, "\n").split("\n");
}

/**
 * What differs between `mine` (the editor) and `theirs` (the other version).
 * Line endings are not counted as a difference. Very large changes are
 * reported as whole blocks rather than matched line by line.
 */
export function diffText(mine: string, theirs: string, preview = PREVIEW_LINES): TextDiff {
  const a = split(mine);
  const b = split(theirs);
  let head = 0;
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1;
  let tail = 0;
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail += 1;
  const midA = a.slice(head, a.length - tail);
  const midB = b.slice(head, b.length - tail);

  const all: DiffLine[] = [];
  if (midA.length * midB.length > MAX_CELLS) {
    midA.forEach((text, index) => all.push({ type: "removed", line: head + index + 1, text }));
    midB.forEach((text, index) => all.push({ type: "added", line: head + index + 1, text }));
  } else {
    // Longest common subsequence over the part that differs.
    const rows = midA.length + 1;
    const cols = midB.length + 1;
    const table = new Uint32Array(rows * cols);
    for (let i = midA.length - 1; i >= 0; i -= 1) {
      for (let j = midB.length - 1; j >= 0; j -= 1) {
        table[i * cols + j] =
          midA[i] === midB[j] ? (table[(i + 1) * cols + j + 1] ?? 0) + 1 : Math.max(table[(i + 1) * cols + j] ?? 0, table[i * cols + j + 1] ?? 0);
      }
    }
    let i = 0;
    let j = 0;
    while (i < midA.length || j < midB.length) {
      if (i < midA.length && j < midB.length && midA[i] === midB[j]) {
        i += 1;
        j += 1;
      } else if (j >= midB.length || (i < midA.length && (table[(i + 1) * cols + j] ?? 0) >= (table[i * cols + j + 1] ?? 0))) {
        all.push({ type: "removed", line: head + i + 1, text: midA[i] ?? "" });
        i += 1;
      } else {
        all.push({ type: "added", line: head + j + 1, text: midB[j] ?? "" });
        j += 1;
      }
    }
  }
  const added = all.filter((line) => line.type === "added").length;
  const removed = all.length - added;
  return { same: all.length === 0, added, removed, lines: all.slice(0, preview), more: all.length > preview };
}

function lines(count: number): string {
  return count === 1 ? "1 line" : `${count} lines`;
}

/**
 * One sentence about a diff: what the other version has and lacks. `it` names
 * the other version and `mine` the one it is compared with.
 */
export function describeDiff(diff: TextDiff, it = "It", mine = "yours"): string {
  if (diff.same) return `${it} has the same text as ${mine}.`;
  if (diff.added && diff.removed) return `${it} has ${lines(diff.added)} ${mine} does not, and lacks ${lines(diff.removed)} of ${mine}.`;
  if (diff.added) return `${it} has ${lines(diff.added)} ${mine} does not.`;
  return `${it} lacks ${lines(diff.removed)} of ${mine}.`;
}
