import { useBenchCount } from "../modules/bench/useBenchCount";
import type { ScreenId } from "../lib/screens";

/** Counts shown beside sidebar items. A module adds its own hook here. */
export function useNavBadges(): Partial<Record<ScreenId, number | undefined>> {
  return { bench: useBenchCount() };
}
