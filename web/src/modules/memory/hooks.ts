// Memory's queries, its place on the shared live feed, and the owner's changes to facts.

import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { memoryApi } from "./api";
import type { ActivityPage, Candidate, CandidateFilter, CandidatesPage, Fact, FactsPage, Note, SourcesResponse } from "./api";
import { predict, settleInPages } from "./lib/candidates";
import type { CandidatePages } from "./lib/candidates";
import { dropNoteFact, dropPagedFact, factsQuery, patchNoteFact, patchPagedFact, putFactBack } from "./lib/facts";
import type { FactFilters, FactPages, FactPatch } from "./lib/facts";
import { touchesMining, touchesSources, withSourceSwitched } from "./lib/mining";
import { changeFrom } from "./lib/status";
import { useSettingActions } from "../../hooks/useSettings";

export const MEMORY_KEY = ["memory"] as const;
export const TREE_KEY = ["memory", "tree"] as const;
export const STATUS_KEY = ["memory", "status"] as const;
export const NOTES_KEY = ["memory", "note"] as const;
export const FACTS_KEY = ["memory", "facts"] as const;
export const HISTORIES_KEY = ["memory", "history"] as const;
export const SOURCES_KEY = ["memory", "sources"] as const;
export const CANDIDATES_KEY = ["memory", "candidates"] as const;
export const ACTIVITY_KEY = ["memory", "activity"] as const;
export const candidatesKey = (filter: CandidateFilter) => ["memory", "candidates", filter] as const;
// The Bench module's queries all start with this. Its badge is read again after a decision made here.
const BENCH_KEY = ["bench"] as const;
export const noteKey = (path: string) => ["memory", "note", path] as const;
export const historyKey = (path: string) => ["memory", "history", path] as const;
export const versionKey = (path: string, version: string) => ["memory", "version", path, version] as const;
export const factsKey = (filters: FactFilters) => ["memory", "facts", factsQuery(filters)] as const;

// A save prints an event for the note and one for each fact, and a mining pass
// several more: wait for them to stop before reading anything again.
const FEED_SETTLE_MS = 250;
const PAGE_SIZE = 50;
// The Bench holds at most 100 of memory's cards, so one page is all of them.
const BENCH_PAGE = 200;
// What waits to be read is counted by the worker about once a minute, with no event.
const SOURCES_REFRESH_MS = 60_000;

/** Reads everything about one note again: its text, its facts and its history. */
export function refreshNote(client: QueryClient, path: string): void {
  void client.invalidateQueries({ queryKey: noteKey(path) });
  void client.invalidateQueries({ queryKey: historyKey(path) });
}

/** Reads the mining panels again: the sources, the proposed facts and the timeline. */
export function refreshMining(client: QueryClient): void {
  void client.invalidateQueries({ queryKey: SOURCES_KEY });
  void client.invalidateQueries({ queryKey: CANDIDATES_KEY });
  void client.invalidateQueries({ queryKey: ACTIVITY_KEY });
}

/** Reads the lists again: the tree, the status, every list of facts, and the mining panels. */
export function refreshLists(client: QueryClient): void {
  void client.invalidateQueries({ queryKey: TREE_KEY });
  void client.invalidateQueries({ queryKey: STATUS_KEY });
  void client.invalidateQueries({ queryKey: FACTS_KEY });
  refreshMining(client);
}

/**
 * Keeps the screen current from the app's shared live feed. `onMoved` is told
 * when a note was renamed, so the screen can follow the one that is open.
 */
export function useMemoryFeed(onMoved: (from: string, to: string) => void): void {
  const client = useQueryClient();
  const moved = useRef(onMoved);
  moved.current = onMoved;

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let miningTimer: ReturnType<typeof setTimeout> | undefined;
    const stop = subscribeFeed({
      onEvent: (event) => {
        const change = changeFrom(event);
        if (!change) {
          // A threshold, a switch or the role's model changed, here or in Settings.
          if (touchesSources(event)) void client.invalidateQueries({ queryKey: SOURCES_KEY });
          // A decision on one of memory's own Bench cards changes no note by itself.
          if (touchesMining(event)) {
            clearTimeout(miningTimer);
            miningTimer = setTimeout(() => refreshMining(client), FEED_SETTLE_MS);
          }
          return;
        }
        // One read of the lists (the mining panels among them) for the whole burst.
        clearTimeout(timer);
        clearTimeout(miningTimer);
        timer = setTimeout(() => refreshLists(client), FEED_SETTLE_MS);
        change.notes.forEach((path) => refreshNote(client, path));
        if (change.movedTo) moved.current(change.movedTo.from, change.movedTo.to);
      },
      onReconnect: () => void client.invalidateQueries({ queryKey: MEMORY_KEY }),
    });
    return () => {
      clearTimeout(timer);
      clearTimeout(miningTimer);
      stop();
    };
  }, [client]);
}

export function useTree() {
  return useQuery({ queryKey: TREE_KEY, queryFn: memoryApi.tree });
}

export function useStatus(enabled = true) {
  return useQuery({ queryKey: STATUS_KEY, queryFn: memoryApi.status, enabled });
}

export function useNote(path: string | null) {
  return useQuery({ queryKey: noteKey(path ?? ""), queryFn: () => memoryApi.note(path ?? ""), enabled: path !== null });
}

export function useHistory(path: string | null) {
  return useQuery({ queryKey: historyKey(path ?? ""), queryFn: () => memoryApi.history(path ?? ""), enabled: path !== null });
}

export function useVersion(path: string, version: string | null) {
  return useQuery({
    queryKey: versionKey(path, version ?? ""),
    queryFn: () => memoryApi.version(path, version ?? ""),
    enabled: version !== null,
    // A saved version never changes.
    staleTime: Infinity,
  });
}

/** Facts across the whole Library, most recently changed first, a page at a time. */
export function useAllFacts(filters: FactFilters, enabled = true) {
  return useInfiniteQuery({
    queryKey: factsKey(filters),
    queryFn: ({ pageParam }) => memoryApi.facts({ ...factsQuery(filters), limit: PAGE_SIZE, before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: FactsPage) => last.next_before ?? undefined,
    placeholderData: keepPreviousData,
    enabled,
  });
}

// ---- mining ----

export function useSources() {
  return useQuery({ queryKey: SOURCES_KEY, queryFn: memoryApi.sources, refetchInterval: SOURCES_REFRESH_MS });
}

/** Proposed facts in one state, newest first, a page at a time. */
export function useCandidates(filter: CandidateFilter, enabled = true) {
  return useInfiniteQuery({
    queryKey: candidatesKey(filter),
    queryFn: ({ pageParam }) => memoryApi.candidates({ state: filter, limit: filter === "bench" ? BENCH_PAGE : PAGE_SIZE, before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: CandidatesPage) => last.next_before ?? undefined,
    enabled,
  });
}

export function useActivity() {
  return useInfiniteQuery({
    queryKey: ACTIVITY_KEY,
    queryFn: ({ pageParam }) => memoryApi.activity({ limit: PAGE_SIZE, before: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: ActivityPage) => last.next_before ?? undefined,
  });
}

/** Everything a decision on a proposed fact can change: the lists, every open note, and the Bench. */
export function refreshAfterDecision(client: QueryClient): void {
  refreshLists(client);
  void client.invalidateQueries({ queryKey: NOTES_KEY });
  void client.invalidateQueries({ queryKey: HISTORIES_KEY });
  void client.invalidateQueries({ queryKey: BENCH_KEY });
}

export type CandidateActions = {
  /** Saves it as a confirmed fact, or confirms one saved automatically. */
  keep: (candidate: Candidate) => Promise<boolean>;
  /** `reason` is required. The fact is never proposed again; if it was saved, it is forgotten. */
  decline: (candidate: Candidate, reason: string) => Promise<boolean>;
};

/**
 * Keep and decline. Each shows on screen at once and is put back with a toast
 * if the server refuses. The server closes the Bench card itself, so the Bench
 * is not called. Neither can be undone through the API, so no Undo is offered.
 */
export function useCandidateActions(): CandidateActions {
  const client = useQueryClient();
  const toast = useToast();

  return useMemo(() => {
    const everyList = (change: (data: CandidatePages, filter: CandidateFilter | null) => CandidatePages) => {
      for (const [key, data] of client.getQueriesData<CandidatePages>({ queryKey: CANDIDATES_KEY })) {
        if (!data) continue;
        const filter = typeof key[2] === "string" ? (key[2] as CandidateFilter) : null;
        client.setQueryData<CandidatePages>(key, change(data, filter));
      }
    };

    const run = async (candidate: Candidate, action: "keep" | "decline", reason: string | null, done: string): Promise<boolean> => {
      // A read already on its way would bring back the list from before the click.
      await client.cancelQueries({ queryKey: CANDIDATES_KEY });
      const snapshot = client.getQueriesData<CandidatePages>({ queryKey: CANDIDATES_KEY });
      const predicted = predict(candidate, action, reason, Date.now());
      everyList((data, filter) => settleInPages(data, filter, candidate, predicted));
      try {
        await (action === "keep" ? memoryApi.keepCandidate(candidate.id) : memoryApi.declineCandidate(candidate.id, reason ?? ""));
        toast.success(done);
        return true;
      } catch (error) {
        snapshot.forEach(([key, data]) => client.setQueryData(key, data));
        toast.error(errorDetail(error));
        return false;
      } finally {
        refreshAfterDecision(client);
      }
    };

    return {
      keep: (candidate) => run(candidate, "keep", null, candidate.needs_review ? "Confirmed." : "Kept: saved as a confirmed fact."),
      decline: (candidate, reason) =>
        run(candidate, "decline", reason, candidate.fact_id && candidate.state === "saved" ? "Declined, and the saved fact was forgotten." : "Declined. It will not be proposed again."),
    };
  }, [client, toast]);
}

/** Switches a source on or off. It is an ordinary setting, saved the way Settings saves one. */
export function useSourceSwitch(): (source: { id: string; setting: string }, on: boolean) => Promise<void> {
  const client = useQueryClient();
  const settings = useSettingActions();

  return useMemo(
    () => async (source, on) => {
      await client.cancelQueries({ queryKey: SOURCES_KEY });
      client.setQueryData<SourcesResponse>(SOURCES_KEY, (data) => (data ? withSourceSwitched(data, source.id, on) : data));
      // Shows a toast and puts the setting back itself if the server refuses.
      await settings.save(source.setting, on);
      // Whether mining is working, and why not, can change with one switch.
      await client.invalidateQueries({ queryKey: SOURCES_KEY });
    },
    [client, settings],
  );
}

// ---- the owner's changes to a fact ----

export type FactActions = {
  /** Confirms a fact, or takes the confirmation back. */
  setConfirmed: (fact: Fact, confirmed: boolean) => Promise<boolean>;
  setKind: (fact: Fact, kind: string) => Promise<boolean>;
  setText: (fact: Fact, text: string) => Promise<boolean>;
  /** Removes the fact's line from its note and retires it. */
  forget: (fact: Fact, reason: string) => Promise<boolean>;
};

type Snapshot = [QueryKey, unknown][];

/**
 * Each change shows on screen at once, is put back with a toast if the server
 * refuses, and offers Undo once it has been made.
 */
export function useFactActions(): FactActions {
  const client = useQueryClient();
  const toast = useToast();

  return useMemo(() => {
    const everywhere = (inNote: (note: Note) => Note, inPages: (pages: FactPages) => FactPages) => {
      client.setQueriesData<Note>({ queryKey: NOTES_KEY }, (note) => (note ? inNote(note) : note));
      client.setQueriesData<FactPages>({ queryKey: FACTS_KEY }, (pages) => (pages ? inPages(pages) : pages));
    };
    const prepare = async (): Promise<Snapshot> => {
      // A read already on its way would bring back the list from before the click.
      await Promise.all([client.cancelQueries({ queryKey: NOTES_KEY }), client.cancelQueries({ queryKey: FACTS_KEY })]);
      return [...client.getQueriesData({ queryKey: NOTES_KEY }), ...client.getQueriesData({ queryKey: FACTS_KEY })];
    };
    const putBack = (snapshot: Snapshot) => snapshot.forEach(([key, data]) => client.setQueryData(key, data));
    const settle = (note: string) => {
      refreshNote(client, note);
      refreshLists(client);
    };

    const change = async (fact: Fact, patch: FactPatch, done: string | null): Promise<boolean> => {
      const snapshot = await prepare();
      everywhere(
        (note) => patchNoteFact(note, fact.id, patch),
        (pages) => patchPagedFact(pages, fact.id, patch),
      );
      try {
        const saved = (await memoryApi.changeFact(fact.id, patch)).fact;
        everywhere(
          (note) => patchNoteFact(note, fact.id, saved),
          (pages) => patchPagedFact(pages, fact.id, saved),
        );
        if (done !== null) {
          const back: FactPatch = {};
          if (patch.text !== undefined) back.text = fact.text;
          if (patch.kind !== undefined) back.kind = fact.kind;
          if (patch.confirmed !== undefined) back.confirmed = fact.confirmed;
          // Undoing is the same change the other way, and is not itself offered for undo.
          toast.success(done, { label: "Undo", run: () => void change(saved, back, null) });
        }
        return true;
      } catch (error) {
        putBack(snapshot);
        toast.error(errorDetail(error));
        return false;
      } finally {
        settle(fact.note);
      }
    };

    const remember = async (fact: Fact, before: string) => {
      try {
        const now = await memoryApi.note(fact.note);
        const text = putFactBack(now.text, before, fact.id);
        if (text === null) {
          toast.error("The fact could not be put back: its line is no longer in the earlier text. Open the note's history to restore it.");
          return;
        }
        const saved = await memoryApi.saveNote(fact.note, text, now.revision);
        client.setQueryData<Note>(noteKey(fact.note), saved);
        toast.success("Remembered again.");
      } catch (error) {
        toast.error(`The fact was not put back. ${errorDetail(error)}`);
      } finally {
        settle(fact.note);
      }
    };

    const forget = async (fact: Fact, reason: string): Promise<boolean> => {
      // The note as it is just before, so the line can be typed back: that revives the same fact.
      let before: string | null = null;
      try {
        before = (await memoryApi.note(fact.note)).text;
      } catch {
        before = null;
      }
      const snapshot = await prepare();
      everywhere(
        (note) => dropNoteFact(note, fact.id),
        (pages) => dropPagedFact(pages, fact.id),
      );
      try {
        await memoryApi.forgetFact(fact.id, reason.trim() || undefined);
        const earlier = before;
        toast.success("Forgotten.", earlier === null ? undefined : { label: "Undo", run: () => void remember(fact, earlier) });
        return true;
      } catch (error) {
        putBack(snapshot);
        toast.error(errorDetail(error));
        return false;
      } finally {
        settle(fact.note);
      }
    };

    return {
      setConfirmed: (fact, confirmed) => change(fact, { confirmed }, confirmed ? "Confirmed." : "No longer marked as confirmed."),
      setKind: (fact, kind) => change(fact, { kind }, `Kind changed to ${kind}.`),
      setText: (fact, text) => change(fact, { text }, "Fact changed."),
      forget,
    };
  }, [client, toast]);
}
