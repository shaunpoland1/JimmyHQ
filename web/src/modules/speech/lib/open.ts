// Which recorders are open right now, across the whole page: the shell's own dictate
// buttons and an app's dictation alike. No browser or React in here.
//
// An app's page may ask the shell to dictate (`hq.speech.dictate`), and that is refused
// while any recording is open, so there is only ever one microphone and one recording
// surface at a time. The shell's own buttons are counted here but not stopped by it.

import type { RecorderState } from "./recorder.ts";

/** True while a recorder holds, or is about to hold, the microphone or a recording. */
export function holdsRecording(state: RecorderState): boolean {
  if (state.phase === "requesting" || state.phase === "recording" || state.phase === "uploading") return true;
  return state.phase === "error" && state.problem.retry;
}

export type OpenSet = {
  /** Says what one recorder (named by any object) is doing now. */
  note: (who: object, state: RecorderState) => void;
  /** How many recorders are open. */
  count: () => number;
};

export function createOpenSet(): OpenSet {
  const open = new Set<object>();
  return {
    note(who, state) {
      if (holdsRecording(state)) open.add(who);
      else open.delete(who);
    },
    count: () => open.size,
  };
}

/** The one set for this page. */
export const OPEN_RECORDINGS: OpenSet = createOpenSet();
