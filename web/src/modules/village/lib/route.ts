// The Village's place in the address bar: `#/village` is the roster,
// `#/village/<id>` one agent, `#/village/<id>/chat` one of its tabs.

export const TABS = ["overview", "chat", "diary", "memory", "files", "integrations", "browser", "activity"] as const;
export type Tab = (typeof TABS)[number];
export const TAB_LABELS: Record<Tab, string> = {
  overview: "Overview",
  chat: "Chat",
  diary: "Diary",
  memory: "Memory",
  files: "Files",
  integrations: "Integrations",
  browser: "Browser",
  activity: "Activity",
};
/** Tabs whose content is not built yet. Every tab is built, so this is empty; add a tab here while it is a stub. */
export const COMING: readonly Tab[] = [];

const AGENT_ID = /^[a-z0-9][a-z0-9-]{0,39}$/;

export type VillageRoute = { agent: null } | { agent: string; tab: Tab };

export function parseVillageRoute(sub: string | null): VillageRoute {
  const [id, tab] = (sub ?? "").split("/").filter(Boolean);
  if (!id || !AGENT_ID.test(id)) return { agent: null };
  return { agent: id, tab: (TABS as readonly string[]).includes(tab ?? "") ? (tab as Tab) : "overview" };
}

export function villageSub(route: VillageRoute): string | null {
  if (route.agent === null) return null;
  return route.tab === "overview" ? route.agent : `${route.agent}/${route.tab}`;
}

/** The tab an arrow key moves to from `current`, wrapping at the ends; null for any other key. */
export function tabAfterKey(key: string, current: Tab): Tab | null {
  const at = TABS.indexOf(current);
  if (key === "ArrowRight") return TABS[(at + 1) % TABS.length] ?? null;
  if (key === "ArrowLeft") return TABS[(at - 1 + TABS.length) % TABS.length] ?? null;
  if (key === "Home") return TABS[0];
  if (key === "End") return TABS[TABS.length - 1] ?? null;
  return null;
}
