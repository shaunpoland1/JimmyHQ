import { useQuery } from "@tanstack/react-query";
import { ApiError, request } from "../api";
import { NO_NAMES, ROSTER_KEY, ROSTER_PATH, chosenNames } from "./agentNames";
import type { ChosenNames } from "./agentNames";

// The roster's key and address are the Village screen's own (`agentNames.ts`), so the two share one
// cached answer and one request, and the Village's live updates refresh this too. They are kept
// there so Tasks and the Bench do not reach into the Village module.

const KEEP_MS = 60_000;

async function fetchRoster(): Promise<unknown> {
  try {
    return await request<unknown>("GET", ROSTER_PATH);
  } catch (error) {
    // A server with the Village off has no roster: nobody has chosen a name.
    if (error instanceof ApiError && error.status === 404) return [];
    throw error;
  }
}

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 1;
}

/**
 * The names agents chose, by id, for any screen that shows an agent known only by its id. One
 * cached read of the roster, never one per row. While it is loading, or when the Village is off,
 * empty or cannot be reached, it is empty and every agent shows its working name.
 */
export function useAgentNames(): ChosenNames {
  const roster = useQuery({ queryKey: ROSTER_KEY, queryFn: fetchRoster, staleTime: KEEP_MS, retry });
  return roster.data === undefined ? NO_NAMES : chosenNames(roster.data);
}
