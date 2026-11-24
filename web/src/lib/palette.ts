// Command palette filtering.

export type Command = {
  id: string;
  title: string;
  group: string;
  keywords?: readonly string[];
};

function isSubsequence(needle: string, haystack: string): boolean {
  let index = 0;
  for (const char of haystack) {
    if (char === needle[index]) index += 1;
    if (index === needle.length) return true;
  }
  return needle.length === 0;
}

function scoreToken(token: string, title: string, extra: string): number {
  if (title.startsWith(token)) return 4;
  if (title.split(/[\s:/-]+/).some((word) => word.startsWith(token))) return 3;
  if (title.includes(token)) return 2;
  if (extra.split(/\s+/).some((word) => word.startsWith(token))) return 1.5;
  if (extra.includes(token)) return 1;
  if (token.length >= 2 && isSubsequence(token, title)) return 0.5;
  return 0;
}

/**
 * Filters and ranks commands for a query. Every whitespace-separated token
 * must match; an empty query returns everything in the original order.
 */
export function filterCommands<T extends Command>(commands: readonly T[], query: string): T[] {
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return [...commands];
  const scored: { command: T; score: number; index: number }[] = [];
  commands.forEach((command, index) => {
    const title = command.title.toLowerCase();
    const extra = [command.group, ...(command.keywords ?? [])].join(" ").toLowerCase();
    let total = 0;
    for (const token of tokens) {
      const score = scoreToken(token, title, extra);
      if (score === 0) return;
      total += score;
    }
    scored.push({ command, score: total, index });
  });
  scored.sort((a, b) => b.score - a.score || a.index - b.index);
  return scored.map((entry) => entry.command);
}

/** Moves a highlighted index up or down a list, wrapping at the ends. */
export function stepIndex(current: number, delta: number, length: number): number {
  if (length <= 0) return 0;
  return (((current + delta) % length) + length) % length;
}
