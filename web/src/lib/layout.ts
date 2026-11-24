// Applying the owner's layout settings to the shell's built-in lists.

/**
 * Returns the items named in `order`, in that order. Ids that match nothing
 * are ignored, and an item left out of `order` is hidden. While settings are
 * still loading (`order` is not a list) every item is shown in its built-in order.
 */
export function pickOrdered<T extends { id: string }>(items: readonly T[], order: unknown): T[] {
  if (!Array.isArray(order)) return [...items];
  const byId = new Map(items.map((item) => [item.id, item]));
  const picked: T[] = [];
  for (const id of order) {
    const item = typeof id === "string" ? byId.get(id.trim().toLowerCase()) : undefined;
    if (item && !picked.includes(item)) picked.push(item);
  }
  return picked;
}

/** A boolean setting's value, or `fallback` while settings are still loading. */
export function settingFlag(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}
