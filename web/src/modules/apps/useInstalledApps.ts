import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useSyncExternalStore } from "react";
import { ApiError, request } from "../../api";
import { subscribeFeed } from "../../lib/feed";
import { affectsInstalled, navItems, parseInstalled, refreshMs, shownBadge } from "./lib/installed";
import type { AppNavItem, InstalledApp, PageBadge } from "./lib/installed";

export const INSTALLED_APPS_KEY = ["apps", "installed"] as const;

type Installed = { apps: InstalledApp[]; refreshMs: number };

async function fetchInstalled(): Promise<Installed> {
  try {
    const raw = await request<unknown>("GET", "/api/apps");
    return { apps: parseInstalled(raw), refreshMs: refreshMs(raw) };
  } catch (error) {
    // A server without the apps module has no apps.
    if (error instanceof ApiError && error.status === 404) return { apps: [], refreshMs: refreshMs(null) };
    throw error;
  }
}

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 2;
}

// What each app's page said with `ui.badge`, and whether its frame is still open. While it is
// open the page's number wins; after, the program's own number (`ui.count`, carried by the
// list) takes over, and an app with none keeps the page's last.
const badges = new Map<string, PageBadge>();
const badgeListeners = new Set<() => void>();
let badgeSnapshot: ReadonlyMap<string, PageBadge> = new Map();

function publishBadges(): void {
  badgeSnapshot = new Map(badges);
  for (const listener of badgeListeners) listener();
}

export function setAppBadge(appId: string, count: number): void {
  badges.set(appId, { count, open: true });
  publishBadges();
}

/** The app's frame closed: what its page said stays only for an app whose program has no number of its own. */
export function releaseAppBadge(appId: string): void {
  const said = badges.get(appId);
  if (!said?.open) return;
  badges.set(appId, { ...said, open: false });
  publishBadges();
}

function subscribeBadges(listener: () => void): () => void {
  badgeListeners.add(listener);
  return () => badgeListeners.delete(listener);
}

export type AppNavEntry = AppNavItem & { badge: number | undefined };

export type InstalledApps = {
  /** Every installed app, switched on or not. */
  apps: readonly InstalledApp[];
  /** One sidebar line per screen of each app that is switched on. */
  items: readonly AppNavEntry[];
  loading: boolean;
  failed: boolean;
  /** Asks the server for the list again, for a Try again button. */
  retry: () => void;
  /** True while the list is being asked for again. */
  retrying: boolean;
};

/**
 * The one thing the shell knows about apps: which are installed and which
 * screens they add. Kept current from the live feed.
 */
export function useInstalledApps(): InstalledApps {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: INSTALLED_APPS_KEY,
    queryFn: fetchInstalled,
    staleTime: 30_000,
    retry,
    // An app's count changes with no event, so the list is read again now and then; how
    // often is the owner's setting (`apps.count_refresh_s`), carried by the list itself.
    refetchInterval: (latest) => latest.state.data?.refreshMs ?? refreshMs(null),
  });
  const pages = useSyncExternalStore(subscribeBadges, () => badgeSnapshot);

  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: INSTALLED_APPS_KEY });
    return subscribeFeed({
      onEvent: (event) => {
        if (affectsInstalled(event)) refresh();
      },
      onReconnect: refresh,
    });
  }, [client]);

  const apps = query.data?.apps;
  const { refetch, isFetching } = query;
  return useMemo(() => {
    const list = apps ?? [];
    const said = new Map(list.map((app) => [app.id, app.count]));
    return {
      apps: list,
      items: navItems(list).map((item) => ({ ...item, badge: shownBadge(pages.get(item.appId), said.get(item.appId) ?? null) })),
      loading: query.isPending,
      failed: query.isError,
      retry: () => void refetch(),
      retrying: isFetching,
    };
  }, [apps, pages, query.isPending, query.isError, refetch, isFetching]);
}
