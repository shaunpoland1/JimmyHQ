// Which packages are on which tab, and in what order. Pure: no DOM.

const RANK = { problem: 0, out_for_delivery: 1, pickup: 2, on_its_way: 3, waiting: 4, not_found: 5, expired: 6, delivered: 7 };

/** Where a status sorts: the ones that need you first. Unknown statuses go last. */
export function rankOf(status) {
  return Object.hasOwn(RANK, status) ? RANK[status] : 99;
}

/** The tab a package belongs on: "delivered" for a delivered one, else "way". */
export function tabOf(row) {
  return row?.status === "delivered" ? "delivered" : "way";
}

/** The packages for one tab, in the order they are shown. */
export function forTab(rows, tab) {
  const mine = (rows ?? []).filter((row) => tabOf(row) === tab);
  if (tab === "delivered") return mine.sort((a, b) => (b.delivered_ms ?? 0) - (a.delivered_ms ?? 0) || b.id - a.id);
  const news = (row) => row.event_ms ?? row.created ?? 0;
  return mine.sort((a, b) => rankOf(a.status) - rankOf(b.status) || news(b) - news(a) || b.id - a.id);
}

/** How many packages are on each tab. */
export function counts(rows) {
  const out = { way: 0, delivered: 0 };
  for (const row of rows ?? []) out[tabOf(row)] += 1;
  return out;
}

/** How many need you: out for delivery, or with a problem. The sidebar shows this. */
export function loud(rows) {
  return (rows ?? []).filter((row) => row.status === "out_for_delivery" || row.status === "problem").length;
}

/** The empty-state sentence for a tab. */
export function emptyWords(tab, anyPackages) {
  if (tab === "delivered") return anyPackages ? "Nothing has been delivered yet. Delivered packages stay here." : "Nothing here yet. Packages you have received are listed here.";
  return anyPackages ? "Nothing is on its way. Everything you added has been delivered." : "No packages yet. Press Add a package, and Packages keeps an eye on it for you.";
}

/**
 * The button for a number the tracking service no longer knows, or null. Registering it
 * again uses another place of quota, so the label says so and it is never the main button.
 */
export function registerAgain(row) {
  return row?.held ? { label: "Register again (uses quota)", kind: "" } : null;
}
