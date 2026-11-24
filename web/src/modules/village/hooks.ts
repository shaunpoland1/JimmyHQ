// The Village's queries and its place on the shared live feed.
//
// The roster, priorities and chat change with Tape events, so they are read again
// when one arrives. Vitals are not on the Tape (they move every turn), so anything
// that shows them is also read again on a gentle interval.

import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { SETTINGS_QUERY_KEY, useSettingsQuery } from "../../hooks/useSettings";
import { subscribeFeed } from "../../lib/feed";
import { villageApi } from "./api";
import type { ChatPage, VillageEvent } from "./api";
import { refreshEvery } from "./lib/browser";
import { mergeMessages, olderCursor } from "./lib/chat";
import { PAGE, kindsOf, mayHaveMore, mergeEvents, olderEdge } from "./lib/events";
import type { Group } from "./lib/events";
import { cleanPath } from "./lib/files";
import type { Scope } from "./lib/files";
import { POLL_MS } from "./lib/commands";
import { headcountCap, showVitals, touchFrom, touchesVillageSettings } from "./lib/live";

export const VILLAGE_KEY = ["village"] as const;
export const AGENTS_KEY = ["village", "agents"] as const;
export const PRIORITIES_KEY = ["village", "priorities"] as const;
export const PROCESS_KEY = ["village", "process"] as const;
export const CAPABILITIES_KEY = ["village", "capabilities"] as const;
export const agentKey = (id: string) => ["village", "agent", id] as const;
export const chatKey = (id: string) => ["village", "chat", id] as const;
export const browserKey = (id: string) => ["village", "browser", id] as const;
export const commandKey = (id: string) => ["village", "command", id] as const;

// A birth prints several events at once: read once for the whole burst.
const FEED_SETTLE_MS = 250;
/** How often vitals are read again while the screen is open. */
export const VITALS_REFRESH_MS = 20_000;
const PROCESS_REFRESH_MS = 15_000;

/** Keeps the screen current from the app's shared live feed. Mount once, in `VillageView`. */
export function useVillageFeed(): void {
  const client = useQueryClient();
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stop = subscribeFeed({
      onEvent: (event) => {
        if (touchesVillageSettings(event)) {
          void client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
          return;
        }
        const touch = touchFrom(event);
        if (!touch) return;
        if (touch.priorities) void client.invalidateQueries({ queryKey: PRIORITIES_KEY });
        if (touch.chat && touch.agent) void client.invalidateQueries({ queryKey: chatKey(touch.agent) });
        if (touch.agent) void client.invalidateQueries({ queryKey: agentKey(touch.agent) });
        if (touch.roster) {
          clearTimeout(timer);
          timer = setTimeout(() => void client.invalidateQueries({ queryKey: AGENTS_KEY }), FEED_SETTLE_MS);
        }
      },
      onReconnect: () => void client.invalidateQueries({ queryKey: VILLAGE_KEY }),
    });
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [client]);
}

/** Everyone, living and retired, in the order they arrived. */
export function useAgents() {
  return useQuery({ queryKey: AGENTS_KEY, queryFn: villageApi.agents, refetchInterval: VITALS_REFRESH_MS });
}

export function useAgent(id: string | null, live = true) {
  return useQuery({
    queryKey: agentKey(id ?? ""),
    queryFn: () => villageApi.agent(id ?? ""),
    enabled: id !== null,
    refetchInterval: live ? VITALS_REFRESH_MS : false,
    retry: false,
  });
}

/** What an agent can be allowed to use, with what is not set up yet. Read afresh each time a setup is opened. */
export function useCapabilities() {
  return useQuery({ queryKey: CAPABILITIES_KEY, queryFn: villageApi.capabilities });
}

/** One agent's browser, read again gently: often while it is in use, seldom while it is closed. */
export function useBrowser(id: string) {
  return useQuery({
    queryKey: browserKey(id),
    queryFn: () => villageApi.browser(id),
    refetchInterval: (query) => refreshEvery(query.state.data),
    retry: false,
  });
}

export function usePriorities() {
  return useQuery({ queryKey: PRIORITIES_KEY, queryFn: villageApi.priorities });
}

/** The settings this screen respects: whether vitals are shown, and the most agents allowed. */
export function useVillageSettings(): { vitals: boolean; cap: number | null } {
  const settings = useSettingsQuery();
  return useMemo(() => ({ vitals: showVitals(settings.data?.settings), cap: headcountCap(settings.data?.settings) }), [settings.data]);
}

/** Whether the village process is alive: true, false, or null when the server does not say. */
export function useVillageProcess(watch = true): boolean | null {
  const status = useQuery({ queryKey: PROCESS_KEY, queryFn: api.status, refetchInterval: watch ? PROCESS_REFRESH_MS : false, enabled: watch });
  const found = status.data?.processes?.find((process) => process.name === "village");
  return found ? found.ok : null;
}

/** One agent's chat, newest page first; older pages are fetched on request. */
export function useChat(id: string) {
  const query = useInfiniteQuery({
    queryKey: chatKey(id),
    queryFn: ({ pageParam }) => villageApi.chat(id, pageParam),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last: ChatPage) => olderCursor(last),
    retry: false,
  });
  const messages = useMemo(() => mergeMessages(query.data?.pages ?? []), [query.data]);
  return { query, messages, asleep: Boolean(query.data?.pages[0]?.asleep) };
}

/** Follows a birth or retirement until the village process has carried it out. */
export function useCommand(id: string | null) {
  const [startedAt, setStartedAt] = useState(() => Date.now());
  useEffect(() => {
    if (id) setStartedAt(Date.now());
  }, [id]);
  const query = useQuery({
    queryKey: commandKey(id ?? ""),
    queryFn: () => villageApi.command(id ?? ""),
    enabled: id !== null,
    refetchInterval: (latest) => (latest.state.data?.done ? false : POLL_MS),
    gcTime: 0,
  });
  const now = useNow(POLL_MS, id !== null && !query.data?.done);
  return { state: query.data, error: query.error, elapsedMs: Math.max(0, now - startedAt) };
}

/** The current time, read again every `everyMs` while `running`. For "wakes in 20 minutes". */
export function useNow(everyMs = 30_000, running = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs, running]);
  return now;
}

// ---- what an agent keeps: diary, memory, snapshots, files and its event log ----

/** How often what changes only at night, or by the village process, is read again. */
const KEPT_REFRESH_MS = 30_000;
const EVENTS_REFRESH_MS = 15_000;
const ROOM_REFRESH_MS = 20_000;

export const diaryKey = (id: string, day: string) => ["village", "diary", id, day || "latest"] as const;
export const memoryKey = (id: string) => ["village", "memory", id] as const;
export const snapshotsKey = (id: string) => ["village", "snapshots", id] as const;
export const nightsKey = (id: string) => ["village", "nights", id] as const;
export const eventsKey = (id: string, group: Group) => ["village", "events", id, group] as const;
export const fileListKey = (scope: Scope, id: string, path: string) => ["village", "files", scope, id, path] as const;
export const fileKey = (scope: Scope, id: string, path: string) => ["village", "file", scope, id, path] as const;
export const BREAKROOM_KEY = ["village", "breakroom"] as const;
export const BOARD_KEY = ["village", "board"] as const;

/** One day of the diary, or the latest when `day` is blank. The list of days comes with it. */
export function useDiary(id: string, day: string) {
  return useQuery({ queryKey: diaryKey(id, day), queryFn: () => villageApi.diary(id, day), refetchInterval: KEPT_REFRESH_MS, retry: false, placeholderData: keepPreviousData });
}

export function useMemory(id: string) {
  return useQuery({ queryKey: memoryKey(id), queryFn: () => villageApi.memory(id), refetchInterval: KEPT_REFRESH_MS, retry: false });
}

export function useSnapshots(id: string, enabled = true) {
  return useQuery({ queryKey: snapshotsKey(id), queryFn: () => villageApi.snapshots(id), refetchInterval: KEPT_REFRESH_MS, retry: false, enabled });
}

/** The sorts the village has done for this agent, to say what each night did. */
export function useNights(id: string, enabled = true) {
  return useQuery({
    queryKey: nightsKey(id),
    queryFn: () => villageApi.events(id, { kinds: ["consolidation"], limit: 500 }),
    refetchInterval: KEPT_REFRESH_MS,
    retry: false,
    enabled,
  });
}

/** Everything that changes when a rollback or a night finishes. */
export function refreshKept(client: ReturnType<typeof useQueryClient>, id: string): void {
  void client.invalidateQueries({ queryKey: ["village", "diary", id] });
  void client.invalidateQueries({ queryKey: memoryKey(id) });
  void client.invalidateQueries({ queryKey: snapshotsKey(id) });
  void client.invalidateQueries({ queryKey: nightsKey(id) });
  void client.invalidateQueries({ queryKey: agentKey(id) });
  void client.invalidateQueries({ queryKey: ["village", "events", id] });
}

/** A folder of the agent's own files, or of the shared folders. */
export function useFileList(scope: Scope, id: string, path: string, enabled = true) {
  return useQuery({
    queryKey: fileListKey(scope, id, path),
    queryFn: async () => {
      if (scope === "own") return villageApi.files(id, cleanPath(path));
      const answer = await villageApi.shared(cleanPath(path));
      if ("items" in answer) return answer;
      throw new Error("That is a file, not a folder.");
    },
    refetchInterval: KEPT_REFRESH_MS,
    retry: false,
    enabled,
  });
}

/** One text file. The shared route answers with the text when the path is a file. */
export function useFileText(scope: Scope, id: string, path: string | null) {
  return useQuery({
    queryKey: fileKey(scope, id, path ?? ""),
    queryFn: async () => {
      const clean = cleanPath(path ?? "");
      if (scope === "own") return villageApi.file(id, clean);
      const answer = await villageApi.shared(clean);
      if ("text" in answer) return answer;
      throw new Error("That is a folder, not a file.");
    },
    enabled: path !== null,
    retry: false,
  });
}

/**
 * An agent's event log, newest first. The newest page is read again every few seconds;
 * older pages are asked for one at a time, by the id of the oldest event held, and kept.
 */
export function useEvents(id: string, group: Group) {
  const kinds = useMemo(() => kindsOf(group), [group]);
  const head = useQuery({
    queryKey: eventsKey(id, group),
    queryFn: () => villageApi.events(id, { kinds, limit: PAGE }),
    refetchInterval: EVENTS_REFRESH_MS,
    retry: false,
  });
  const [older, setOlder] = useState<{ key: string; pages: VillageEvent[][]; more: boolean | null }>({ key: "", pages: [], more: null });
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<unknown>(null);
  const scope = `${id}|${group}`;
  const mine = older.key === scope ? older : { key: scope, pages: [], more: null };

  const events = useMemo(() => mergeEvents([head.data?.events ?? [], ...mine.pages]), [head.data, mine.pages]);
  const more = mine.more ?? (head.data ? mayHaveMore(head.data.events.length) : false);

  const loadOlder = useCallback(async () => {
    const edge = olderEdge(events);
    if (!edge || loadingOlder) return;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await villageApi.events(id, { kinds, before: edge, limit: PAGE });
      setOlder((current) => {
        const base = current.key === scope ? current : { key: scope, pages: [], more: null };
        return { key: scope, pages: [...base.pages, page.events], more: mayHaveMore(page.events.length) };
      });
    } catch (error) {
      setOlderError(error);
    } finally {
      setLoadingOlder(false);
    }
  }, [events, id, kinds, loadingOlder, scope]);

  return { head, events, more, loadOlder, loadingOlder, olderError };
}

export function useBreakroom() {
  return useQuery({ queryKey: BREAKROOM_KEY, queryFn: villageApi.breakroom, refetchInterval: ROOM_REFRESH_MS, retry: false });
}

export function useBoard() {
  return useQuery({ queryKey: BOARD_KEY, queryFn: villageApi.board, refetchInterval: ROOM_REFRESH_MS, retry: false });
}
