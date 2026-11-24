import { useMemo } from "react";
import { cardAppLink } from "./lib/cardLink";
import type { CardAppLink } from "./lib/cardLink";
import { useInstalledApps } from "./useInstalledApps";

/** The "Open <app>" link for a Bench card, or null when its filer is not an installed app that can be opened. */
export function useCardAppLink(filedBy: string): CardAppLink | null {
  const { apps } = useInstalledApps();
  return useMemo(() => cardAppLink(filedBy, apps), [filedBy, apps]);
}
