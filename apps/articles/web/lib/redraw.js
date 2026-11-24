// When the program says something changed: which part of the page to draw again. Pure.

/** Views with fields the owner may be typing in. */
export const QUIET = new Set(["new", "sites", "ideas"]);
/** Of those, the views that keep what the owner typed in the page's state as it is typed. */
export const HELD = new Set(["new", "ideas"]);

/**
 * "all" to draw the whole view again, or "nav" to draw only the counts and leave the view
 * as it is until the owner is done typing. While the owner is in one of the view's fields
 * (a search box too) only the counts are drawn, and the view once they leave it. Otherwise
 * a view without a form is always drawn again, so nothing shows stale data, and a view
 * with a form is drawn again unless the owner has typed in it since it was drawn (a view
 * that keeps what was typed does not count that). `where` is {view, typed, focused}.
 */
export function afterChange({ view, typed = false, focused = false }) {
  if (focused) return "nav";
  if (!QUIET.has(view)) return "all";
  return typed && !HELD.has(view) ? "nav" : "all";
}
