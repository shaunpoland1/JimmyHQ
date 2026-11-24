// Sorting by a column heading: first press ascending, second descending, third back to the
// view's own order. The view's declared sort is where it starts from.

export type Direction = "asc" | "desc";

/** The sort in force: the person's choice if they made one, else the view's. */
export function effectiveSort(chosen: string[] | null, declared: string[] | undefined): string[] | undefined {
  return chosen ?? declared;
}

/** Which way a column is sorted by the first key, or null when it is not the first. */
export function directionOf(sort: string[] | undefined, field: string): Direction | null {
  const first = sort?.[0];
  if (first === field) return "asc";
  if (first === `-${field}`) return "desc";
  return null;
}

/** What a press on a heading chooses next, as the person's own choice (null: back to the view's). */
export function nextSort(chosen: string[] | null, declared: string[] | undefined, field: string): string[] | null {
  const now = directionOf(effectiveSort(chosen, declared), field);
  if (now === null) return [field];
  if (now === "asc") return [`-${field}`];
  return null;
}

export function ariaSort(direction: Direction | null): "ascending" | "descending" | "none" {
  return direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none";
}
