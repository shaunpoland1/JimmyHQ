// The chat module's data hooks: the queries, the live refresh and the
// owner's changes to a conversation.

import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { ApiError, errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { chatApi } from "./api";
import type { Conversation, ConversationChange, ConversationDeleted, ConversationList, ConversationPage, NewConversation, OffRecordDeleted } from "./api";
import { CHAT_KEY, CHAT_LIST_KEY, CHAT_OPTIONS_KEY, conversationKey, listKey, listsKey } from "./keys";
import { conversationTitle, placeConversation } from "./lib/format";
import type { ListPages } from "./lib/format";
import { archivedNotice, deletedForGoodNotice } from "./lib/delete";
import { deletedNotice, dropConversation, isChatReceipt, offTheFeed, threadRefreshMs } from "./lib/record";
import type { ThreadPages } from "./lib/reply";

// ---- live refresh ----
// One subscription to the app's shared feed for the whole module.

const SETTLE_MS = 150;
/** While another tab's reply is being written, look again this often in case its ending is missed. */
const ELSEWHERE_REFRESH_MS = 5000;

const watchers = new Map<QueryClient, number>();
let stopFeed: (() => void) | null = null;
let pending: ReturnType<typeof setTimeout> | null = null;
let stale = { list: false, options: false, offRecord: false, conversations: new Set<string>() };

/** An open conversation that is off the record: what the feed cannot speak for. */
function offRecordThread(query: { queryKey: readonly unknown[]; state: { data: unknown } }): boolean {
  if (query.queryKey[0] !== "chat" || query.queryKey[1] !== "conversation") return false;
  const data = query.state.data as ThreadPages | undefined;
  return offTheFeed(data?.pages[0]?.conversation);
}

function refreshSoon(): void {
  if (pending) return;
  pending = setTimeout(() => {
    pending = null;
    const now = stale;
    stale = { list: false, options: false, offRecord: false, conversations: new Set() };
    for (const client of watchers.keys()) {
      if (now.list) void client.invalidateQueries({ queryKey: CHAT_LIST_KEY });
      if (now.options) void client.invalidateQueries({ queryKey: CHAT_OPTIONS_KEY });
      for (const id of now.conversations) void client.invalidateQueries({ queryKey: conversationKey(id) });
      if (now.offRecord) void client.invalidateQueries({ predicate: offRecordThread });
    }
  }, SETTLE_MS);
}

function onFeedEvent(kind: string, payload: Record<string, unknown>): void {
  if (kind.startsWith("chat.")) {
    stale.list = true;
    if (typeof payload.conversation_id === "string") stale.conversations.add(payload.conversation_id);
    refreshSoon();
    return;
  }
  if (isChatReceipt(kind, payload)) {
    // Another tab may have had a reply written off the record: the receipt is the only sign of it.
    stale.list = true;
    stale.offRecord = true;
    refreshSoon();
    return;
  }
  // What the composer offers follows the providers, the roles and the chat settings.
  const key = payload.key;
  const setting = (kind === "setting.updated" || kind === "setting.reset") && typeof key === "string" && (key.startsWith("chat.") || key.startsWith("models.") || key.startsWith("lookup."));
  if (setting || kind.startsWith("model.provider.") || kind.startsWith("model.role.")) {
    stale.options = true;
    refreshSoon();
  }
}

function watch(client: QueryClient): () => void {
  watchers.set(client, (watchers.get(client) ?? 0) + 1);
  stopFeed ??= subscribeFeed({
    onEvent: (event) => onFeedEvent(event.kind, event.payload ?? {}),
    // After a reconnect, anything may have been missed.
    onReconnect: () => {
      for (const each of watchers.keys()) void each.invalidateQueries({ queryKey: CHAT_KEY });
    },
  });
  return () => {
    const left = (watchers.get(client) ?? 1) - 1;
    if (left > 0) watchers.set(client, left);
    else watchers.delete(client);
    if (watchers.size > 0) return;
    if (pending) clearTimeout(pending);
    pending = null;
    stopFeed?.();
    stopFeed = null;
  };
}

/** Keeps every chat query in step with changes made anywhere. */
export function useChatFeed(): void {
  const client = useQueryClient();
  useEffect(() => watch(client), [client]);
}

// ---- queries ----

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 2;
}

export function useChatOptions() {
  return useQuery({ queryKey: CHAT_OPTIONS_KEY, queryFn: chatApi.options, staleTime: 30_000, retry });
}

/**
 * The conversation list. `search` is the title search as `searchQuery`
 * leaves it: the server looks through every conversation, not only the pages
 * loaded here. While a new search is on its way the last list stays up.
 */
export function useConversationList(archived: boolean, search = "") {
  return useInfiniteQuery({
    queryKey: listKey(archived, search),
    queryFn: ({ pageParam }) => chatApi.list(archived, pageParam, search),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: ConversationList) => last.next ?? undefined,
    placeholderData: keepPreviousData,
    retry,
    // What is done off the record in another window is not on the live feed: look again on coming back to this one.
    refetchOnWindowFocus: true,
  });
}

/** One conversation and its messages: the newest page first, older pages on demand. */
export function useConversation(id: string | null, listening: boolean) {
  return useInfiniteQuery({
    queryKey: conversationKey(id ?? ""),
    queryFn: ({ pageParam }) => chatApi.conversation(id ?? "", pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last: ConversationPage) => last.older ?? undefined,
    enabled: id !== null,
    retry,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => threadRefreshMs(query.state.data?.pages[0]?.conversation, listening, ELSEWHERE_REFRESH_MS),
  });
}

// ---- the owner's changes ----

export type ChatActions = {
  /** Starts a conversation. Throws the server's refusal. */
  create: (body: NewConversation) => Promise<Conversation>;
  /** Changes what answers, the effort or the local-only flag; shown at once, put back if refused. */
  change: (conversation: Conversation, body: ConversationChange) => Promise<Conversation | null>;
  rename: (conversation: Conversation, title: string) => Promise<Conversation | null>;
  archive: (conversation: Conversation) => Promise<Conversation | null>;
  restore: (conversation: Conversation) => Promise<Conversation | null>;
  /** Flips the record switch for what is said from now on; shown at once, put back if refused. */
  setOnRecord: (conversation: Conversation, onRecord: boolean) => Promise<Conversation | null>;
  /** Deletes what is off the record, for good. Null if it was refused. */
  deleteOffRecord: (conversation: Conversation) => Promise<OffRecordDeleted | null>;
  /** Deletes the whole conversation for good. Null if it was refused. */
  deleteForGood: (conversation: Conversation) => Promise<ConversationDeleted | null>;
};

function quoted(conversation: Conversation): string {
  const title = conversationTitle(conversation);
  return title.length > 60 ? `“${title.slice(0, 57)}…”` : `“${title}”`;
}

/** Writes a conversation as the server returned it into every list and its own pages. */
function store(client: QueryClient, conversation: Conversation): void {
  for (const archived of [false, true]) {
    // One list per search that has been made; the last part of its key is the search.
    for (const [key] of client.getQueriesData<ListPages>({ queryKey: listsKey(archived) })) {
      const search = typeof key[3] === "string" ? key[3] : "";
      client.setQueryData<ListPages>(key, (data) => placeConversation(data, conversation, archived, search));
    }
  }
  client.setQueryData<ThreadPages>(conversationKey(conversation.id), (data) =>
    data ? { ...data, pages: data.pages.map((page) => ({ ...page, conversation })) } : data,
  );
}

export function useChatActions(): ChatActions {
  const client = useQueryClient();
  const toast = useToast();

  return useMemo(() => {
    const run = async (before: Conversation, guess: Conversation | null, send: () => Promise<Conversation>): Promise<Conversation | null> => {
      // A read already on its way would bring back the conversation from before the change.
      await client.cancelQueries({ queryKey: CHAT_LIST_KEY });
      if (guess) store(client, guess);
      try {
        const saved = await send();
        store(client, saved);
        return saved;
      } catch (error) {
        if (guess) store(client, before);
        toast.error(errorDetail(error));
        return null;
      } finally {
        void client.invalidateQueries({ queryKey: CHAT_LIST_KEY });
        void client.invalidateQueries({ queryKey: conversationKey(before.id) });
      }
    };

    const create = async (body: NewConversation) => {
      const conversation = await chatApi.start(body);
      client.setQueryData<ThreadPages>(conversationKey(conversation.id), {
        pages: [{ conversation, messages: [], older: null, unanswered: false, can_regenerate: false }],
        pageParams: [undefined],
      });
      client.setQueryData<ListPages>(listKey(false), (data) => placeConversation(data, conversation, false));
      return conversation;
    };

    const change = (conversation: Conversation, body: ConversationChange) =>
      run(conversation, { ...conversation, ...body }, () => chatApi.change(conversation.id, body));

    const rename = async (conversation: Conversation, title: string): Promise<Conversation | null> => {
      const next = title.trim().replace(/\s+/g, " ");
      if (!next || next === conversation.title) return conversation;
      const saved = await run(conversation, { ...conversation, title: next, title_source: "owner" }, () => chatApi.change(conversation.id, { title: next }));
      if (saved) {
        const was = conversation.title;
        // A title cannot be set back to nothing, so an untitled conversation has no way back.
        toast.success(
          `Renamed to ${quoted(saved)}.`,
          was ? { label: "Undo", run: () => void run(saved, { ...saved, title: was }, () => chatApi.change(saved.id, { title: was })) } : undefined,
        );
      }
      return saved;
    };

    const archive = async (conversation: Conversation): Promise<Conversation | null> => {
      const saved = await run(conversation, { ...conversation, archived: true }, () => chatApi.archive(conversation.id));
      if (saved) toast.success(archivedNotice(quoted(saved), saved.deletes_at, Date.now()), { label: "Undo", run: () => void run(saved, { ...saved, archived: false }, () => chatApi.restore(saved.id)) });
      return saved;
    };

    const restore = async (conversation: Conversation): Promise<Conversation | null> => {
      const saved = await run(conversation, { ...conversation, archived: false }, () => chatApi.restore(conversation.id));
      if (saved) toast.success(`Restored ${quoted(saved)}.`, { label: "Undo", run: () => void run(saved, { ...saved, archived: true }, () => chatApi.archive(saved.id)) });
      return saved;
    };

    const setOnRecord = (conversation: Conversation, onRecord: boolean) =>
      run(conversation, { ...conversation, on_record: onRecord }, () => chatApi.change(conversation.id, { on_record: onRecord }));

    const deleteOffRecord = async (conversation: Conversation): Promise<OffRecordDeleted | null> => {
      await client.cancelQueries({ queryKey: CHAT_KEY });
      let gone = false;
      try {
        const result = await chatApi.deleteOffRecord(conversation.id);
        if (result.conversation) store(client, result.conversation);
        else {
          for (const [key] of client.getQueriesData<ListPages>({ queryKey: CHAT_LIST_KEY })) {
            client.setQueryData<ListPages>(key, (data) => dropConversation(data, conversation.id));
          }
          // The thread's own cache is removed by the screen once it has moved off it: a read now would find it gone.
          gone = true;
        }
        toast.success(deletedNotice(result.deleted, result.conversation === null));
        return result;
      } catch (error) {
        toast.error(errorDetail(error));
        return null;
      } finally {
        // The messages themselves have changed, and nothing on the feed will say so.
        void client.invalidateQueries({ queryKey: CHAT_LIST_KEY });
        if (!gone) void client.invalidateQueries({ queryKey: conversationKey(conversation.id) });
      }
    };

    const deleteForGood = async (conversation: Conversation): Promise<ConversationDeleted | null> => {
      await client.cancelQueries({ queryKey: CHAT_KEY });
      try {
        const result = await chatApi.deleteForGood(conversation.id);
        for (const [key] of client.getQueriesData<ListPages>({ queryKey: CHAT_LIST_KEY })) {
          client.setQueryData<ListPages>(key, (data) => dropConversation(data, conversation.id));
        }
        // The thread's own cache is removed by the screen once it has moved off it: a read now would find it gone.
        toast.success(deletedForGoodNotice(result));
        void client.invalidateQueries({ queryKey: CHAT_LIST_KEY });
        return result;
      } catch (error) {
        toast.error(errorDetail(error));
        // Refused: it is still there, and may have changed while we asked.
        void client.invalidateQueries({ queryKey: CHAT_LIST_KEY });
        void client.invalidateQueries({ queryKey: conversationKey(conversation.id) });
        return null;
      }
    };

    return { create, change, rename, archive, restore, setOnRecord, deleteOffRecord, deleteForGood };
  }, [client, toast]);
}
