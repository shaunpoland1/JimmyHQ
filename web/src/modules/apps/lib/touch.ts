// When the owner last touched an app's page, as far as the shell can tell.
//
// The frame is another origin, so the shell never sees a click inside it. What it can see:
//   - `navigator.userActivation.isActive`: true for a few seconds after a click or key
//     press anywhere in this tab, the shell's own page or the frame. It says nothing about
//     where, or when exactly.
//   - The shell's own clicks and key presses (`shellInput`): events inside the frame never
//     reach the shell's document, so any that do were the shell's.
//   - Focus moving into the frame (the shell window's `blur` with the frame as the active
//     element): the owner clicked or tabbed into it.
//
// So a touch is credited to the frame only when the browser says there is an activation,
// the frame holds the focus, and the shell has had no click or key of its own since the
// focus went in (or long enough ago that its activation has run out). And one activation
// is one touch: while the same activation is still alive, asking again does not make the
// touch newer. A new touch is seen once the activation was seen to end, or ACTIVATION_MS
// after the touch was first seen, when the one before must have run out.
//
// What the shell cannot see is a second click in the frame while the first one's activation
// is still alive: the browser only says "active", not "active again", and events inside the
// frame never reach the shell. The page's bridge script (sdk/js/bridge.js) tells the shell on
// each click or key press (`ui.touch`), but the frame is the app's own page, so a page can send
// that word without any click, as often as it likes. It is therefore never taken as proof of a
// new touch: `ui.touch` only makes the shell look at the browser's activation now (`observe`),
// which is what any message from the page does. It cannot make a touch newer, start a new run of
// saves, or let dictation or Settings be asked for again. The cost is that a second real click
// within ACTIVATION_MS of the first, while the first one's activation lives, is the same touch:
// it can save only what is left of that touch's `apps.save_parts`, and a run that has used them
// all waits for the browser's activation to run out (at most ACTIVATION_MS) before the next
// click starts a new one. There is no signal a page cannot forge that would tell the two apart.
//
// And, in a browser without `userActivation`, nothing but focus moving in is taken as the
// touch.

import type { Activation } from "./uploads.ts";

/** How long a browser keeps one click's activation at the longest (Chrome and Firefox: 5 seconds; Safari less). */
export const ACTIVATION_MS = 5000;

export type Touch = {
  /** When the current touch of the frame was first seen. */
  touchedAt: number | null;
  /** When the activation that touch belongs to was first seen; null once it was seen to end. */
  activeSince: number | null;
  /** The shell's own last click or key press. */
  shellInputAt: number | null;
  /** When the focus last moved into the frame. */
  frameEnteredAt: number | null;
};

export function createTouch(): Touch {
  return { touchedAt: null, activeSince: null, shellInputAt: null, frameEnteredAt: null };
}

/** The shell's own page saw a click or a key press: an activation now may be the shell's. */
export function shellInput(touch: Touch, now: number): void {
  touch.shellInputAt = now;
}

/** Whether an activation seen now can be the frame's rather than the shell's. */
export function frameOwnsActivation(touch: Touch, frameFocused: boolean, now: number): boolean {
  if (!frameFocused) return false;
  const shell = touch.shellInputAt;
  if (shell === null) return true;
  if (touch.frameEnteredAt !== null && touch.frameEnteredAt >= shell) return true;
  return now - shell > ACTIVATION_MS;
}

function credit(touch: Touch, now: number): void {
  const same = touch.activeSince !== null && now >= touch.activeSince && now - touch.activeSince <= ACTIVATION_MS;
  if (!same) {
    touch.activeSince = now;
    touch.touchedAt = now;
  }
}

/**
 * Looks at the browser's activation as the frame says something. Moves `touchedAt` only for a
 * new touch the frame owns: one whose activation was seen to end, or that has outlived
 * ACTIVATION_MS. Nothing the page says can make it move sooner, however often it says it.
 */
export function observe(touch: Touch, activation: Activation, frameFocused: boolean, now: number): void {
  if (!activation) return;
  if (!activation.isActive) {
    touch.activeSince = null;
    return;
  }
  if (frameOwnsActivation(touch, frameFocused, now)) credit(touch, now);
}

/**
 * What the page's `ui.touch` ("the owner just clicked or pressed a key") does: the shell looks
 * at the browser's activation now, as it does for any message from the page. The page's word is
 * not proof of anything, so it adds nothing: no new touch, no new run of saves.
 */
export function hint(touch: Touch, activation: Activation, frameFocused: boolean, now: number): void {
  observe(touch, activation, frameFocused, now);
}

/** The focus moved into the frame. A touch, if the browser says there is an activation or cannot say. */
export function focusEntered(touch: Touch, activation: Activation, now: number): void {
  touch.frameEnteredAt = now;
  if (!activation || activation.isActive) credit(touch, now);
}
