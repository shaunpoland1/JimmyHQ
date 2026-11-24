import type { KeyContext } from "../lib/keys";

/**
 * Where a key press landed, for the tasks screen's shortcuts. Null when the
 * key is not the screen's to handle: something else already used it, or focus
 * is in the sidebar, the top strip or the dock.
 */
export function keyContextOf(event: KeyboardEvent, root: HTMLElement | null): KeyContext | null {
  if (event.defaultPrevented) return null;
  const target = event.target instanceof HTMLElement ? event.target : null;
  const main = root?.closest("main") ?? root;
  if (target && target !== document.body && !main?.contains(target)) return null;
  const tag = target?.tagName ?? "";
  return {
    typing: tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable === true,
    onControl: tag === "BUTTON" || tag === "A" || tag === "SUMMARY",
    dialogOpen: document.querySelector('[role="dialog"]') !== null,
  };
}
