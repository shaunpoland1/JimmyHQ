import { useBenchBoard } from "./useBench";

/** How many Bench cards need the owner, for the sidebar badge. Owned by the bench module. */
export function useBenchCount(): number | undefined {
  return useBenchBoard().data?.counts.needs;
}
