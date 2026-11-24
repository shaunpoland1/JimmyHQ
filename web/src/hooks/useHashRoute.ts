import { useCallback, useEffect, useState } from "react";
import { formatHash, hashForPath, parseHash, sameRoute } from "../lib/route";
import type { Route } from "../lib/route";

/** The route to start from. A plain path such as `/settings` is moved to `/#/settings`. */
function startRoute(): Route {
  const moved = hashForPath(window.location.pathname, window.location.hash);
  if (moved !== null) window.history.replaceState(null, "", `/${moved}`);
  return parseHash(window.location.hash);
}

/** The current screen, kept in state and synced with `location.hash`. */
export function useHashRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(startRoute);

  useEffect(() => {
    const sync = () => {
      const next = parseHash(window.location.hash);
      setRoute((current) => (sameRoute(current, next) ? current : next));
    };
    window.addEventListener("hashchange", sync);
    // Normalise an empty or unknown hash to its canonical form.
    const canonical = formatHash(parseHash(window.location.hash));
    if (window.location.hash !== canonical) window.history.replaceState(null, "", canonical);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const navigate = useCallback((next: Route) => {
    setRoute((current) => (sameRoute(current, next) ? current : next));
    const hash = formatHash(next);
    if (window.location.hash !== hash) window.location.hash = hash;
  }, []);

  return [route, navigate];
}
