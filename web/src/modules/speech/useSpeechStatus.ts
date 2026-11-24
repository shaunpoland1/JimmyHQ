import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { subscribeFeed } from "../../lib/feed";
import { SPEECH_STATUS_KEY, SPEECH_STATUS_STALE_MS, speechApi } from "./api";
import { affectsSpeechStatus } from "./lib/settings";

const PREPARING_POLL_MS = 2_000;

/**
 * The speech status, kept current: it is asked for again whenever the live
 * feed reports a change to a Voice setting, the key, or a transcription, and
 * every few seconds while a model is being downloaded.
 */
export function useSpeechStatus() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: SPEECH_STATUS_KEY,
    queryFn: speechApi.status,
    staleTime: SPEECH_STATUS_STALE_MS,
    // A model download says nothing on the live feed when it ends, so ask until it has.
    refetchInterval: (current) => (current.state.data?.download?.preparing ? PREPARING_POLL_MS : false),
  });

  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: SPEECH_STATUS_KEY });
    return subscribeFeed({
      onEvent: (event) => {
        if (affectsSpeechStatus(event)) refresh();
      },
      // Anything may have changed while the feed was down.
      onReconnect: refresh,
    });
  }, [client]);

  return query;
}
