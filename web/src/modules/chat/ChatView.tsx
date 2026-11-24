import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, ArrowLeft, MessageSquare, Pencil, TriangleAlert, X } from "lucide-react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, errorDetail } from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { useHashRoute } from "../../hooks/useHashRoute";
import { usePersistentState } from "../../hooks/usePersistentState";
import { useToast } from "../../hooks/useToast";
import { formatHash } from "../../lib/route";
import { useLookupStatus } from "../lookup/useLookupStatus";
import { sameTarget } from "../models/lib/picker";
import type { ModelPickerValue } from "../models/ModelPicker";
import type { Conversation, ConversationChange } from "./api";
import { conversationKey, listKey } from "./keys";
import "./chat.css";
import { Composer } from "./Composer";
import { ConversationList } from "./ConversationList";
import { ConversationMenu } from "./ConversationMenu";
import { DeleteForGoodDialog } from "./DeleteForGoodDialog";
import { useChatActions, useChatFeed, useChatOptions, useConversation, useConversationList } from "./hooks";
import { KEY_WIDGET_SELECTOR, canSend, focusTakesKeys, offersModelSettings, screenCommand } from "./lib/compose";
import {
  NEW_ROUTE,
  conversationTitle,
  searchQuery,
  filterConversations,
  flattenList,
  fromPickerTarget,
  modelReady,
  parseChatRoute,
  parseStoredId,
  toPickerOptions,
  toPickerTarget,
} from "./lib/format";
import { saveDrafts, savedDrafts } from "./lib/drafts";
import { localToggle, memoryAllowed, newConversationMemory } from "./lib/memory";
import { ARCHIVE_HINT } from "./lib/delete";
import { DELETED_HELP, DELETED_TITLE, nextAfterLeaving } from "./lib/leave";
import { deleteWarning, lastOpenAfter, newConversationRecord } from "./lib/record";
import type { ListPages } from "./lib/format";
import { flattenPages, threadView } from "./lib/reply";
import { inferWeb, searchTarget, webHelp, webStart, webToggle } from "./lib/web";
import type { StepRow } from "./lib/web";
import { startReply, stopReply, useLiveIds, useLiveReply } from "./streams";
import { Thread } from "./Thread";

const CLOCK_TICK_MS = 30_000;
const TITLE_MAX = 120;
const NEW_KEY = NEW_ROUTE;
const MODELS_HREF = formatHash({ screen: "settings", sub: "models" });
const LAST_OPEN_KEY = "jimmyhq.chat.last";
const WEB_SETTINGS_HREF = formatHash({ screen: "settings", sub: "lookup/lookup.enabled" });
const NO_STEPS: readonly StepRow[] = [];

/** How long the search box is left alone before the server is asked. */
const SEARCH_SETTLE_MS = 250;

type Refusal = { status: number; detail: string };

function TitleEditor({ conversation, onRename }: { conversation: Conversation; onRename: (title: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const input = useRef<HTMLInputElement>(null);
  /** Set once the edit has ended, so the blur that follows does not save it again. */
  const ended = useRef(false);
  const opener = useRef<HTMLButtonElement>(null);
  const untitled = conversation.title.trim() === "";

  const begin = () => {
    ended.current = false;
    setValue(conversation.title);
    setEditing(true);
    requestAnimationFrame(() => {
      // select() alone does not move the focus in every browser.
      input.current?.focus();
      input.current?.select();
    });
  };
  const end = (save: boolean, refocus: boolean) => {
    if (ended.current) return;
    ended.current = true;
    setEditing(false);
    if (save) onRename(value);
    if (refocus) requestAnimationFrame(() => opener.current?.focus());
  };

  if (editing) {
    return (
      <input
        ref={input}
        className="input !h-8 min-w-0 flex-1"
        value={value}
        maxLength={TITLE_MAX}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            end(true, true);
          } else if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            end(false, true);
          }
        }}
        onBlur={() => end(true, false)}
        aria-label="Conversation title. Enter saves, Escape cancels."
      />
    );
  }
  return (
    <>
      <h1 className={`min-w-0 flex-1 truncate text-sm font-semibold ${untitled ? "italic text-text-faint" : "text-text"}`}>{conversationTitle(conversation)}</h1>
      {conversation.archived ? null : (
        <button ref={opener} type="button" className="icon-btn" onClick={begin} aria-label="Rename conversation" title="Rename">
          <Pencil size={14} aria-hidden="true" />
        </button>
      )}
    </>
  );
}

/** Chat: the conversation list beside the open conversation. */
export function ChatView() {
  useChatFeed();
  const client = useQueryClient();
  const toast = useToast();
  const services = useMemo(() => ({ client, toast }), [client, toast]);
  const actions = useChatActions();

  // ---- which conversation is open ----
  const [route, navigate] = useHashRoute();
  // Settings opens over this screen and takes the route; the conversation stays put behind it.
  const [seen, setSeen] = useState(route);
  const [sub, setSub] = useState<string | null>(route.screen === "chat" ? route.sub : null);
  if (seen !== route) {
    setSeen(route);
    if (route.screen === "chat") setSub(route.sub);
  }
  const chatRoute = parseChatRoute(sub);
  const openId = chatRoute.kind === "conversation" ? chatRoute.id : null;
  const draftKey = openId ?? NEW_KEY;
  const [lastId, setLastId] = usePersistentState<string | null>(LAST_OPEN_KEY, null, parseStoredId);

  // Plain #/chat goes back to the conversation that was open last.
  useEffect(() => {
    if (route.screen !== "chat" || chatRoute.kind !== "none" || !lastId) return;
    window.location.replace(formatHash({ screen: "chat", sub: lastId }));
  }, [route.screen, chatRoute.kind, lastId]);

  // Below the two-pane width the list and the conversation are shown one at a time.
  const [pane, setPane] = useState<"list" | "thread">("thread");
  const [search, setSearch] = useState("");
  // The server is asked once the typing pauses; until then the loaded list is narrowed here.
  const wanted = searchQuery(search);
  const [asked, setAsked] = useState(wanted);
  useEffect(() => {
    if (wanted === "") {
      setAsked("");
      return;
    }
    const timer = setTimeout(() => setAsked(wanted), SEARCH_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [wanted]);
  const [showArchived, setShowArchived] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const searchRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const root = useRef<HTMLDivElement>(null);

  // ---- data ----
  const options = useChatOptions();
  const list = useConversationList(showArchived, asked);
  const searchPending = asked !== wanted || list.isPlaceholderData;
  const live = useLiveReply(openId);
  const liveIds = useLiveIds();
  const thread = useConversation(openId, live !== undefined);

  const loadedList = useMemo(() => flattenList(list.data), [list.data]);
  // The server's answer to a search is shown as it came; a list still on its way is narrowed here.
  const shownList = useMemo(() => (searchPending ? filterConversations(loadedList, search) : loadedList), [searchPending, loadedList, search]);

  const pages = thread.data?.pages;
  const newest = pages?.[0];
  const conversation = newest?.conversation ?? null;
  const messages = useMemo(() => flattenPages(pages ?? []), [pages]);
  const view = useMemo(() => threadView(messages, live), [messages, live]);

  // Remembered once it has loaded, and only if some of it is on the record.
  const openConversationId = conversation?.id ?? null;
  const openRecorded = conversation?.recorded ?? false;
  useEffect(() => {
    if (openConversationId === null || openConversationId !== openId) return;
    setLastId((current) => lastOpenAfter(current, { id: openConversationId, recorded: openRecorded }));
  }, [openConversationId, openRecorded, openId, setLastId]);

  const missing = thread.error instanceof ApiError && thread.error.status === 404;
  useEffect(() => {
    // Never go back to a conversation that is not there.
    if (missing && openId !== null && openId === lastId) setLastId(null);
  }, [missing, openId, lastId, setLastId]);

  const busyElsewhere = live === undefined && conversation?.streaming === true;
  const busy = live !== undefined || busyElsewhere;
  // The conversation whose reply, written from another tab, this tab has asked to stop.
  const [stoppingId, setStoppingId] = useState<string | null>(null);
  const stopping = live?.stopping === true || (busyElsewhere && stoppingId === openId);

  // ---- the composer's choices ----
  const pickerOptions = useMemo(() => toPickerOptions(options.data), [options.data]);
  const defaults = options.data?.defaults;
  const maxChars = options.data?.max_message_chars ?? Number.POSITIVE_INFINITY;
  const ready = options.data ? modelReady(options.data) : true;
  // What a conversation that has not been started yet will be started with.
  const [newValue, setNewValue] = useState<ModelPickerValue | null>(null);
  const [newLocalOnly, setNewLocalOnly] = useState<boolean | null>(null);
  const [newOnRecord, setNewOnRecord] = useState<boolean | null>(null);
  const [newUseMemory, setNewUseMemory] = useState<boolean | null>(null);

  const value: ModelPickerValue = useMemo(
    () =>
      conversation
        ? { target: toPickerTarget(conversation.target), effort: conversation.effort }
        : (newValue ?? { target: toPickerTarget(defaults?.target), effort: defaults?.effort ?? null }),
    [conversation, newValue, defaults],
  );
  const localOnly = conversation ? conversation.local_only : (newLocalOnly ?? defaults?.local_only ?? false);
  // A new conversation starts as the setting says unless the switch is flipped first.
  const onRecord = conversation ? conversation.on_record : (newOnRecord ?? defaults?.on_record ?? true);
  const useMemory = conversation ? conversation.use_memory : (newUseMemory ?? defaults?.use_memory ?? true);
  // Whether the model chosen may be sent memory, as the server will decide it.
  const memoryOk = useMemo(() => memoryAllowed(options.data, fromPickerTarget(value.target), localOnly), [options.data, value, localOnly]);
  // The Web switch belongs to each message, so it is kept here, by conversation, as the owner last set it.
  // A conversation opened afresh learns it from its last reply; a new one starts as the setting says.
  const lookup = useLookupStatus();
  const [webChoices, setWebChoices] = useState<Readonly<Record<string, boolean>>>({});
  const localSwitch = useMemo(() => localToggle(options.data), [options.data]);
  const toggle = useMemo(() => webToggle(options.data?.web, lookup.data), [options.data, lookup.data]);
  const inferredWeb = useMemo(() => inferWeb(messages), [messages]);
  const startsWithWeb = webStart(options.data?.web, lookup.data);
  const webWanted = webChoices[draftKey] ?? (openId !== null ? inferredWeb : null) ?? startsWithWeb;
  const webOn = webWanted && !toggle.disabled;
  const webHelpText = useMemo(
    () => webHelp(webOn, { searchTarget: searchTarget(lookup.data?.provider, lookup.data?.provider_label), localOnly }),
    [webOn, lookup.data, localOnly],
  );
  /** The conversation whose off-the-record part the owner has asked to delete, until they confirm or cancel. */
  const [deleting, setDeleting] = useState<Conversation | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  /** The conversation the owner has asked to delete for good, until they confirm or cancel. */
  const [erasing, setErasing] = useState<Conversation | null>(null);
  const [eraseBusy, setEraseBusy] = useState(false);

  // ---- drafts and refusals ----
  const [drafts, setDrafts] = useState(savedDrafts);
  const setDraft = useCallback((key: string, text: string | ((current: string) => string)) => {
    setDrafts((all) => {
      const next = typeof text === "function" ? text(all[key] ?? "") : text;
      const saved = { ...all, [key]: next };
      saveDrafts(saved);
      return saved;
    });
  }, []);
  const text = drafts[draftKey] ?? "";

  const [refusals, setRefusals] = useState<Record<string, Refusal>>({});
  const setRefusal = useCallback((key: string, refusal: Refusal | null) => {
    setRefusals((all) => {
      if (refusal) return { ...all, [key]: refusal };
      if (!(key in all)) return all;
      const { [key]: _gone, ...rest } = all;
      return rest;
    });
  }, []);
  const refusal = refusals[draftKey] ?? null;

  const [creating, setCreating] = useState(false);
  const [follow, setFollow] = useState(0);

  // What the async actions and the key handler read, without re-subscribing.
  const latest = useRef({ openId, draftKey, text, busy, creating, maxChars, value, localOnly, newOnRecord, newUseMemory, conversation, ready, pane, webOn, webUsable: !toggle.disabled });
  latest.current = { openId, draftKey, text, busy, creating, maxChars, value, localOnly, newOnRecord, newUseMemory, conversation, ready, pane, webOn, webUsable: !toggle.disabled };

  const focusComposer = useCallback(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  const open = useCallback(
    (id: string) => {
      navigate({ screen: "chat", sub: id });
      setPane("thread");
      focusComposer();
    },
    [navigate, focusComposer],
  );

  const startNew = useCallback(() => {
    navigate({ screen: "chat", sub: NEW_ROUTE });
    setPane("thread");
    focusComposer();
  }, [navigate, focusComposer]);

  const send = useCallback(async () => {
    const state = latest.current;
    if (state.busy || state.creating || !canSend(state.text, state.maxChars)) return;
    const words = state.text;
    let id = state.openId;
    setRefusal(state.draftKey, null);
    if (id === null) {
      // The conversation is started by its first message, so nothing empty is left behind.
      if (!state.ready) return;
      setCreating(true);
      try {
        const target = fromPickerTarget(state.value.target);
        const made = await actions.create({ ...(target ? { target } : {}), effort: state.value.effort, local_only: state.localOnly, ...newConversationRecord(state.newOnRecord), ...newConversationMemory(state.newUseMemory) });
        id = made.id;
      } catch (error) {
        setRefusal(NEW_KEY, { status: error instanceof ApiError ? error.status : 0, detail: errorDetail(error) });
        return;
      } finally {
        setCreating(false);
      }
      setDraft(NEW_KEY, "");
      setNewValue(null);
      setNewLocalOnly(null);
      setNewOnRecord(null);
      setNewUseMemory(null);
      // What the Web switch said for this first message stays for the conversation.
      if (state.webUsable) {
        const made = id;
        setWebChoices(({ [NEW_KEY]: _new, ...rest }) => ({ ...rest, [made]: state.webOn }));
      } else setWebChoices(({ [NEW_KEY]: _new, ...rest }) => rest);
      navigate({ screen: "chat", sub: id });
    } else setDraft(id, "");
    setFollow((count) => count + 1);
    const started = id;
    const result = await startReply(services, started, { text: words, web: state.webOn });
    if (result.ok) return;
    // Refused: the words go back in the box (unless something new was typed meanwhile).
    setDraft(started, (current) => current || words);
    if (result.detail) setRefusal(started, { status: result.status, detail: result.detail });
  }, [actions, navigate, services, setDraft, setRefusal]);

  const regenerate = useCallback(async () => {
    const state = latest.current;
    if (state.busy || state.openId === null) return;
    const id = state.openId;
    setRefusal(id, null);
    setFollow((count) => count + 1);
    const result = await startReply(services, id, { regenerate: true, web: state.webOn });
    if (!result.ok && result.detail) setRefusal(id, { status: result.status, detail: result.detail });
  }, [services, setRefusal]);

  const onRegenerate = useCallback(() => void regenerate(), [regenerate]);

  const stop = useCallback(() => {
    const id = latest.current.openId;
    if (id === null) return;
    setStoppingId(id);
    void stopReply(services, id).finally(() => setStoppingId((current) => (current === id ? null : current)));
  }, [services]);

  const changeValue = useCallback(
    (next: ModelPickerValue) => {
      const state = latest.current;
      if (!state.conversation) {
        setNewValue(next);
        return;
      }
      const body: ConversationChange = {};
      if (!sameTarget(next.target, state.value.target)) {
        const target = fromPickerTarget(next.target);
        if (target) body.target = target;
      }
      if (next.effort !== state.value.effort) body.effort = next.effort;
      if (Object.keys(body).length > 0) void actions.change(state.conversation, body);
    },
    [actions],
  );

  const changeLocalOnly = useCallback(
    (next: boolean) => {
      const current = latest.current.conversation;
      if (current) void actions.change(current, { local_only: next });
      else setNewLocalOnly(next);
    },
    [actions],
  );

  const changeUseMemory = useCallback(
    (next: boolean) => {
      const current = latest.current.conversation;
      if (current) void actions.change(current, { use_memory: next });
      else setNewUseMemory(next);
    },
    [actions],
  );

  const changeWeb = useCallback((next: boolean) => {
    const key = latest.current.draftKey;
    setWebChoices((all) => ({ ...all, [key]: next }));
  }, []);

  const changeOnRecord = useCallback(
    (next: boolean) => {
      const current = latest.current.conversation;
      if (current) void actions.setOnRecord(current, next);
      else setNewOnRecord(next);
    },
    [actions],
  );

  /** The open conversation has gone from the open list (archived or deleted): show the next one, or the empty state. */
  const leave = useCallback(
    (id: string) => {
      if (latest.current.openId !== id) return;
      const next = nextAfterLeaving(flattenList(client.getQueryData<ListPages>(listKey(false))), id);
      setLastId((current) => (current === id ? next : current));
      if (next) open(next);
      else startNew();
    },
    [client, open, startNew, setLastId],
  );

  const archiveAndLeave = useCallback(
    async (target: Conversation) => {
      const saved = await actions.archive(target);
      if (saved) leave(target.id);
    },
    [actions, leave],
  );

  const confirmDelete = useCallback(async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    const result = await actions.deleteOffRecord(deleting);
    setDeleteBusy(false);
    setDeleting(null);
    if (!result || result.conversation) return;
    // Nothing of it was on the record, so there is nothing left to show.
    setDraft(deleting.id, "");
    leave(deleting.id);
    // Only now that nothing watches it: a read while it was still open would find it gone.
    client.removeQueries({ queryKey: conversationKey(deleting.id) });
  }, [actions, client, deleting, leave, setDraft]);

  const confirmErase = useCallback(async () => {
    if (!erasing) return;
    setEraseBusy(true);
    const result = await actions.deleteForGood(erasing);
    setEraseBusy(false);
    setErasing(null);
    if (!result) return;
    setDraft(erasing.id, "");
    leave(erasing.id);
    // Only now that nothing watches it: a read while it was still open would find it gone.
    client.removeQueries({ queryKey: conversationKey(erasing.id) });
  }, [actions, client, erasing, leave, setDraft]);

  // ---- keys: n starts a conversation, / goes to the search box ----
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const main = root.current?.closest("main") ?? root.current;
      // Keys pressed in the sidebar, the top strip or the dock are not this screen's.
      if (target && target !== document.body && !main?.contains(target)) return;
      const tag = target?.tagName ?? "";
      const command = screenCommand({
        key: event.key,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        altKey: event.altKey,
        repeat: event.repeat,
        // The model picker's open list finds a model by the letters typed: those are not this screen's either.
        typing: focusTakesKeys(tag, target?.isContentEditable === true, target?.closest(KEY_WIDGET_SELECTOR)?.getAttribute("role") ?? null),
        dialogOpen: document.querySelector('[role="dialog"]') !== null,
      });
      if (command === null) return;
      event.preventDefault();
      if (command === "new") startNew();
      else {
        setPane("list");
        requestAnimationFrame(() => searchRef.current?.focus());
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [startNew]);

  // ---- the open conversation ----
  const back = (
    <button type="button" className="icon-btn @2xl/chat:hidden" onClick={() => setPane("list")} aria-label="Back to conversations" title="Conversations">
      <ArrowLeft size={16} aria-hidden="true" />
    </button>
  );

  const notices = (
    <>
      {options.isError ? (
        <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-[13px] text-text-muted">
          <p className="min-w-0 flex-1 basis-48 break-words">The model choices could not be loaded. {errorDetail(options.error)}</p>
          <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={() => void options.refetch()}>
            Try again
          </button>
        </div>
      ) : null}
      {options.data && !ready ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-[13px] text-text-muted">
          <p className="min-w-0 flex-1 basis-48">No model is set up to answer yet. Add one under Settings, Models.</p>
          <a className="btn !h-7 !px-2.5 !text-xs" href={MODELS_HREF}>
            Open Models and roles
          </a>
        </div>
      ) : null}
      {busyElsewhere ? (
        <p role="status" className="rounded-lg border border-border bg-surface px-3 py-2 text-[13px] text-text-muted">
          A reply is being written here from another tab or window. Stop it, or wait for it to finish.
        </p>
      ) : null}
      {refusal ? (
        <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-danger/40 bg-danger/5 px-3 py-2 text-[13px] text-text">
          <TriangleAlert size={14} className="flex-none text-danger" aria-hidden="true" />
          <p className="min-w-0 flex-1 basis-48 break-words">{refusal.detail}</p>
          {offersModelSettings(refusal.status, refusal.detail) ? (
            <a className="btn !h-7 !px-2.5 !text-xs" href={MODELS_HREF}>
              Open Models and roles
            </a>
          ) : null}
          <button type="button" className="icon-btn" onClick={() => setRefusal(draftKey, null)} aria-label="Dismiss this message">
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      ) : null}
    </>
  );

  const composer = (
    <Composer
      text={text}
      onText={(next) => {
        setDraft(draftKey, next);
        if (refusal) setRefusal(draftKey, null);
      }}
      onSend={() => void send()}
      onStop={stop}
      busy={busy}
      stopping={stopping}
      blocked={creating || (openId === null && !ready) || (openId !== null && conversation === null)}
      pickerOptions={pickerOptions}
      pickerDisabled={options.data === undefined || creating || (openId !== null && conversation === null)}
      value={value}
      onValue={changeValue}
      localOnly={localOnly}
      onLocalOnly={changeLocalOnly}
      onRecord={onRecord}
      onOnRecord={changeOnRecord}
      recordDisabled={busy || creating || options.data === undefined || (openId !== null && conversation === null)}
      useMemory={useMemory}
      onUseMemory={changeUseMemory}
      memoryAllowed={memoryOk}
      web={webOn}
      onWeb={changeWeb}
      webToggle={toggle}
      localToggle={localSwitch}
      webHelpText={webHelpText}
      webSettingsHref={WEB_SETTINGS_HREF}
      maxChars={maxChars}
      placeholder={openId === null ? "Ask anything" : "Write a message"}
      inputRef={inputRef}
    />
  );

  let threadPane;
  if (openId === null) {
    threadPane = (
      <>
        <header className="flex h-11 flex-none items-center gap-2 border-b border-border px-3 @2xl/chat:hidden">
          {back}
          <h1 className="min-w-0 flex-1 truncate text-sm font-semibold text-text">New conversation</h1>
        </header>
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-y-auto px-4 py-6">
          <div className="flex w-full max-w-2xl flex-col gap-4">
            <div className="flex flex-col items-center gap-1 text-center">
              <h2 className="text-xl font-semibold text-text">What can I help with?</h2>
              <p className="max-w-md text-sm text-text-muted">
                {!list.isPending && !list.isError && !showArchived && asked === "" && !searchPending && loadedList.length === 0
                  ? "No conversations yet. Your first message starts one."
                  : "Your first message starts a new conversation."}
              </p>
            </div>
            {notices}
            {composer}
          </div>
        </div>
      </>
    );
  } else if (thread.isPending) {
    threadPane = (
      <>
        <header className="flex h-11 flex-none items-center gap-2 border-b border-border px-3 @2xl/chat:hidden">{back}</header>
        <div className="flex min-h-0 flex-1 items-center justify-center text-sm text-text-faint" role="status">
          Loading the conversation…
        </div>
      </>
    );
  } else if (missing) {
    threadPane = (
      <>
        <header className="flex h-11 flex-none items-center gap-2 border-b border-border px-3 @2xl/chat:hidden">{back}</header>
        <div className="min-h-0 flex-1 overflow-y-auto" role="status">
          <EmptyState icon={MessageSquare} title={DELETED_TITLE} description={DELETED_HELP} level={1}>
            <button type="button" className="btn btn-primary" onClick={startNew}>
              New conversation
            </button>
          </EmptyState>
        </div>
      </>
    );
  } else if (conversation === null) {
    threadPane = (
      <>
        <header className="flex h-11 flex-none items-center gap-2 border-b border-border px-3 @2xl/chat:hidden">{back}</header>
        <div className="min-h-0 flex-1 overflow-y-auto" role="alert">
          <EmptyState icon={MessageSquare} title="This conversation could not be opened" description={errorDetail(thread.error)} level={1}>
            <div className="flex flex-wrap justify-center gap-2">
              <button type="button" className="btn" onClick={() => void thread.refetch()} disabled={thread.isFetching}>
                Try again
              </button>
              <button type="button" className="btn btn-primary" onClick={startNew}>
                New conversation
              </button>
            </div>
          </EmptyState>
        </div>
      </>
    );
  } else {
    const current = conversation;
    threadPane = (
      <>
        <header className="flex h-11 flex-none items-center gap-1.5 border-b border-border px-3">
          {back}
          <TitleEditor key={current.id} conversation={current} onRename={(title) => void actions.rename(current, title)} />
          {current.archived ? <span className="chip flex-none">archived</span> : null}
          {current.archived ? (
            <button type="button" className="icon-btn" onClick={() => void actions.restore(current)} aria-label="Restore conversation" title="Restore">
              <ArchiveRestore size={14} aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              className="icon-btn"
              onClick={() => void archiveAndLeave(current)}
              disabled={busy}
              aria-label="Put away conversation"
              title={busy ? "Wait for the reply to finish, or stop it, before putting it away" : ARCHIVE_HINT}
            >
              <Archive size={14} aria-hidden="true" />
            </button>
          )}
          <ConversationMenu key={`menu-${current.id}`} conversation={current} busy={busy} onDeleteOffRecord={() => setDeleting(current)} onDeleteForGood={() => setErasing(current)} />
        </header>
        {thread.isError ? (
          <div role="alert" className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-surface px-4 py-2 text-xs text-text-muted">
            <p className="min-w-0 flex-1 basis-48 break-words">This may be out of date. {errorDetail(thread.error)}</p>
            <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={() => void thread.refetch()}>
              Try again
            </button>
          </div>
        ) : null}
        {view.messages.length === 0 && view.pendingText === null && view.writing === null ? (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <EmptyState icon={MessageSquare} title="Nothing here yet" description="Write a message below to begin." compact />
          </div>
        ) : (
          <Thread
            key={current.id}
            conversationId={current.id}
            view={view}
            hasOlder={thread.hasNextPage}
            loadingOlder={thread.isFetchingNextPage}
            onLoadOlder={() => void thread.fetchNextPage()}
            unanswered={newest?.unanswered === true && !busy}
            canRegenerate={newest?.can_regenerate === true && !busy && !current.archived}
            onRegenerate={onRegenerate}
            stopping={stopping}
            onRecord={current.on_record}
            follow={follow}
            steps={live?.steps ?? NO_STEPS}
          />
        )}
        <div className="flex-none border-t border-border bg-bg px-4 py-3">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
            {current.archived ? (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-surface px-3 py-2 text-[13px] text-text-muted">
                <p className="min-w-0 flex-1 basis-48">This conversation is archived. Restore it to carry on.</p>
                <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={() => void actions.restore(current)}>
                  <ArchiveRestore size={12} aria-hidden="true" />
                  Restore
                </button>
              </div>
            ) : (
              <>
                {notices}
                {composer}
              </>
            )}
          </div>
        </div>
      </>
    );
  }

  return (
    <div ref={root} className="@container/chat flex h-full min-h-0">
      <aside
        aria-label="Conversations"
        className={`min-h-0 w-full flex-none flex-col border-border bg-surface @2xl/chat:flex @2xl/chat:w-72 @2xl/chat:border-r ${pane === "list" ? "flex" : "hidden"}`}
      >
        <ConversationList
          conversations={shownList}
          loaded={loadedList.length}
          searchPending={searchPending}
          loading={list.isPending}
          error={list.isError ? errorDetail(list.error) : null}
          onRetry={() => void list.refetch()}
          hasMore={list.hasNextPage}
          loadingMore={list.isFetchingNextPage}
          onMore={() => void list.fetchNextPage()}
          selectedId={openId}
          liveIds={liveIds}
          now={now}
          search={search}
          onSearch={setSearch}
          searchRef={searchRef}
          archived={showArchived}
          onArchived={setShowArchived}
          onOpen={(item) => open(item.id)}
          onNew={startNew}
          onArchive={(item) => void archiveAndLeave(item)}
          onRestore={(item) => void actions.restore(item)}
        />
      </aside>
      <section
        aria-label={conversation ? `Conversation: ${conversationTitle(conversation)}` : "Conversation"}
        className={`@container min-h-0 min-w-0 flex-1 flex-col @2xl/chat:flex ${pane === "thread" ? "flex" : "hidden"}`}
      >
        {threadPane}
      </section>
      {deleting ? (
        <ConfirmDialog
          title="Delete only what is off the record?"
          message={deleteWarning(deleting)}
          confirmLabel="Delete for good"
          danger
          busy={deleteBusy}
          onConfirm={() => void confirmDelete()}
          onCancel={() => {
            if (!deleteBusy) setDeleting(null);
          }}
        />
      ) : null}
      {erasing ? (
        <DeleteForGoodDialog
          conversation={erasing}
          busy={eraseBusy}
          onConfirm={() => void confirmErase()}
          onCancel={() => {
            if (!eraseBusy) setErasing(null);
          }}
        />
      ) : null}
    </div>
  );
}
