import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { ApiError } from "../../api";
import { subscribeFeed } from "../../lib/feed";
import { LOOKUP_STATUS_KEY, lookupApi } from "./api";
import { affectsLookupStatus } from "./lib/status";

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 2;
}

/**
 * The web lookup status, kept current: asked for again whenever the live feed
 * reports a change to a Web lookup setting. A key saved or cleared here
 * answers with the new status, which the caller puts in the cache itself.
 */
export function useLookupStatus() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: LOOKUP_STATUS_KEY, queryFn: lookupApi.status, staleTime: 30_000, retry });

  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: LOOKUP_STATUS_KEY });
    return subscribeFeed({
      onEvent: (event) => {
        if (affectsLookupStatus(event)) refresh();
      },
      // Anything may have changed while the feed was down.
      onReconnect: refresh,
    });
  }, [client]);

  return query;
}
