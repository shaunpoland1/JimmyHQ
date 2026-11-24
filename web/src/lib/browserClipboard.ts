// The browser's side of copying (clipboard.ts holds the rules, tested with fakes; only a real
// browser can confirm what is here).

import { copyText } from "./clipboard.ts";
import type { ClipboardEnv } from "./clipboard.ts";

/** The browser's own two ways of copying. */
export function browserClipboard(): ClipboardEnv {
  return { clipboard: typeof navigator !== "undefined" ? navigator.clipboard : undefined, copyBySelecting: selectAndCopy };
}

/** Copies `text` for the owner with the browser's clipboard; false means tell them to copy it by hand. */
export function copyToClipboard(text: string): Promise<boolean> {
  return copyText(text, browserClipboard());
}

function selectAndCopy(text: string): boolean {
  const before = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  // Inside the element that has the focus (a dialog traps focus, and a box outside it could not be selected), else the page.
  const host = before?.parentElement ?? document.body;
  const box = document.createElement("textarea");
  box.value = text;
  box.setAttribute("readonly", "");
  box.setAttribute("aria-hidden", "true");
  box.tabIndex = -1;
  box.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none";
  host.appendChild(box);
  try {
    box.focus({ preventScroll: true });
    box.select();
    box.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } finally {
    box.remove();
    before?.focus({ preventScroll: true });
  }
}
