// When the program says something changed: which part of the page to draw again. Pure.

/** Views with fields the owner may be typing in. */
export const QUIET = new Set(["new", "pages"]);
/** Of those, the views that keep what the owner typed in the page's state as it is typed. */
export const HELD = new Set(["new"]);

/**
 * "all" to draw the whole view again, or "nav" to draw only the counts and leave the
 * view as it is until the owner is done typing. A view without fields is always drawn
 * again, so nothing shows stale data; a view with fields is drawn again as long as the
 * owner has not typed in it since it was drawn (New keeps what was typed, so that does
 * not count there) and is not in one of its fields now. `where` is {view, typed, focused}.
 */
export function afterChange({ view, typed = false, focused = false }) {
  if (!QUIET.has(view)) return "all";
  if (focused) return "nav";
  return typed && !HELD.has(view) ? "nav" : "all";
}
