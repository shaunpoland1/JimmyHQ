import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useToast } from "../../hooks/useToast";
import { SPEECH_STATUS_KEY, SPEECH_STATUS_STALE_MS, speechApi } from "./api";
import { createDictation } from "./dictation";
import type { Dictation } from "./dictation";
import type { RecorderState } from "./lib/recorder";
import { DEFAULT_CALLER } from "./types";

type Options = { onText: (text: string) => void; caller?: string };

/** One dictation recorder for a component, released when the component leaves the page. */
export function useDictation({ onText, caller }: Options): { state: RecorderState; dictation: Dictation } {
  const client = useQueryClient();
  const toast = useToast();
  // The controller lives as long as the component; it reads the latest props through this.
  const latest = useRef({ onText, caller, client, toast });
  latest.current = { onText, caller, client, toast };

  const [dictation] = useState(() =>
    createDictation({
      status: (fresh) =>
        latest.current.client.fetchQuery({
          queryKey: SPEECH_STATUS_KEY,
          queryFn: speechApi.status,
          staleTime: fresh ? 0 : SPEECH_STATUS_STALE_MS,
        }),
      transcribe: (audio, filename, name, signal) => speechApi.transcribe(audio, { filename, caller: name, signal }),
      notify: (kind, message, action) => latest.current.toast.show(kind, message, action),
      statusChanged: () => void latest.current.client.invalidateQueries({ queryKey: SPEECH_STATUS_KEY }),
      caller: () => latest.current.caller?.trim().toLowerCase() || DEFAULT_CALLER,
      onText: (text) => latest.current.onText(text),
    }),
  );

  const state = useSyncExternalStore(dictation.subscribe, dictation.getState);

  // Leaving the page stops the recording and lets go of the microphone.
  useEffect(() => () => dictation.cancel(), [dictation]);

  return { state, dictation };
}
