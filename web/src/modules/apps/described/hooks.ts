// The renderer's data hooks: a view's records page by page, the owner's calendar settings
// (read here, at the display edge) and the writes, which answer with a sentence on failure.

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { errorDetail } from "../../../api";
import { useSettingsQuery } from "../../../hooks/useSettings";
import { describedApi } from "./api";
import type { DescribedApi, ListArgs, ListResult, OneResult, Transport, Value } from "./api";
import { joinPages } from "./lib/pages";
import { weekStartIndex } from "./lib/calendar";

export function useDescribedApi(appId: string, transport?: Transport): DescribedApi {
  return useMemo(() => describedApi(appId, transport), [appId, transport]);
}

const key = (appId: string) => ["described", appId] as const;

export function useRecords(appId: string, api: DescribedApi, args: Omit<ListArgs, "after">, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: [...key(appId), "list", args],
    queryFn: ({ pageParam }) => api.list(pageParam ? { ...args, after: pageParam } : args),
    initialPageParam: "",
    getNextPageParam: (page: ListResult) => page.next ?? undefined,
    enabled,
  });
  const data = useMemo(() => (query.data ? joinPages(query.data.pages) : null), [query.data]);
  return {
    data,
    loading: query.isPending,
    error: query.error ? errorDetail(query.error) : null,
    loadMore: () => void query.fetchNextPage(),
    loadingMore: query.isFetchingNextPage,
    reload: () => void query.refetch(),
  };
}

/** The records a link field can point at, for its drop-down. */
export function useLinkOptions(appId: string, api: DescribedApi, table: string) {
  return useQuery({
    queryKey: [...key(appId), "options", table],
    queryFn: () => api.list({ table, limit: 500 }),
    staleTime: 10_000,
  });
}

export type Outcome<T = OneResult> = { ok: true; result: T } | { ok: false; message: string };

export function useRecordActions(appId: string, api: DescribedApi) {
  const client = useQueryClient();
  return useMemo(() => {
    const run = async <T,>(send: () => Promise<T>): Promise<Outcome<T>> => {
      try {
        const result = await send();
        return { ok: true, result };
      } catch (error) {
        return { ok: false, message: errorDetail(error) };
      } finally {
        void client.invalidateQueries({ queryKey: key(appId) });
      }
    };
    return {
      create: (table: string, values: Record<string, Value>) => run(() => api.create(table, values)),
      update: (table: string, id: number, values: Record<string, Value>) => run(() => api.update(table, id, values)),
      remove: (table: string, id: number, unlink = false) => run(() => api.remove(table, id, unlink)),
    };
  }, [api, appId, client]);
}

export type OwnerCalendar = { timeZone: string; weekStart: number };

/** The owner's time zone and the day their week starts on, from settings. */
export function useOwnerCalendar(): OwnerCalendar {
  const settings = useSettingsQuery().data?.settings;
  const read = (name: string) => settings?.find((s) => s.key === name)?.value;
  const zone = read("owner.timezone");
  return { timeZone: typeof zone === "string" ? zone : "", weekStart: weekStartIndex(read("owner.week_starts")) };
}
