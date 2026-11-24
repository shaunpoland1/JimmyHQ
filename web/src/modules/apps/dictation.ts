// The browser's side of `hq.speech.dictate`: the shell's one desk, and the recorder it
// drives, which is the speech module's own (`createDictation`) talking to the same
// `/api/speech/transcribe` as the shell's dictate buttons, with `app=<id>` added so the
// server applies the app's rules. The rules themselves are in lib/dictate.ts.

import type { QueryClient } from "@tanstack/react-query";
import { SPEECH_STATUS_KEY, SPEECH_STATUS_STALE_MS, speechApi } from "../speech/api";
import { createDictation } from "../speech/dictation";
import { OPEN_RECORDINGS } from "../speech/lib/open";
import type { Environment } from "../speech/lib/problems";
import type { SpeechStatus } from "../speech/types";
import { createDesk, dictateCaller, privateProblem } from "./lib/dictate";
import type { DictateRecorder } from "./lib/dictate";

/** The one desk for the whole shell: one app dictation at a time, and none while any recording is open. */
export const DESK = createDesk(() => OPEN_RECORDINGS.count());

type Notify = (kind: "info" | "error", message: string, action?: { label: string; run: () => void }) => void;

/** The speech status, from the shared cache unless `fresh`. */
export function speechStatus(client: QueryClient, fresh = false): Promise<SpeechStatus> {
  return client.fetchQuery({ queryKey: SPEECH_STATUS_KEY, queryFn: speechApi.status, staleTime: fresh ? 0 : SPEECH_STATUS_STALE_MS });
}

/** What this browser, on this page, offers for recording. */
export function browserEnvironment(): Environment {
  return {
    secure: window.isSecureContext,
    microphoneApi: typeof navigator.mediaDevices?.getUserMedia === "function",
    recorderApi: typeof MediaRecorder !== "undefined",
  };
}

/**
 * The shell's recorder for one app's dictation. The words go to `heard` and nowhere else:
 * `onText` is empty, because the desk hands them to the page.
 */
export function appRecorder(
  app: { id: string; private: boolean; maxSeconds: number },
  client: QueryClient,
  notify: Notify,
): (heard: (text: string) => void) => DictateRecorder {
  return (heard) =>
    createDictation({
      status: (fresh) => speechStatus(client, fresh),
      transcribe: async (audio, filename, caller, signal) => {
        const result = await speechApi.transcribe(audio, { filename, caller, signal, app: app.id });
        heard(typeof result.text === "string" ? result.text : "");
        return result;
      },
      notify,
      statusChanged: () => void client.invalidateQueries({ queryKey: SPEECH_STATUS_KEY }),
      caller: () => dictateCaller(app.id),
      onText: () => undefined,
      maxSeconds: () => app.maxSeconds,
      gate: (status) => privateProblem(status, app.private),
    });
}
