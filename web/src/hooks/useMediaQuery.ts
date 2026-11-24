import { useCallback, useSyncExternalStore } from "react";

/** Live result of a CSS media query. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches);
}

/** True when the sidebar becomes a drawer and the dock hides behind a button. */
export function useIsNarrow(): boolean {
  return useMediaQuery("(max-width: 899px)");
}
