import { Archive, ArchiveRestore, Plus, Search } from "lucide-react";
import { useId } from "react";
import type { RefObject } from "react";
import { Switch } from "../../components/Switch";
import type { Conversation } from "./api";
import { ARCHIVED_STAYS, ARCHIVED_WILL_GO, ARCHIVE_HINT, daysUntilDeleted, deletionMark, deletionSentence } from "./lib/delete";
import { MAX_SEARCH, ROW_ACTION_CLASS, conversationTitle, formatListTime } from "./lib/format";
import { OFF_LABEL } from "./lib/record";

type ConversationListProps = {
  /** The conversations to show, already narrowed by the search. */
  conversations: Conversation[];
  /** How many the server has sent for the list, or the search, on screen. */
  loaded: number;
  /** A search has been typed and its answer has not arrived yet. */
  searchPending: boolean;
  loading: boolean;
  /** The server's sentence when the list could not be read. */
  error: string | null;
  onRetry: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  onMore: () => void;
  selectedId: string | null;
  /** Conversations a reply is being written in from this tab. */
  liveIds: ReadonlySet<string>;
  now: number;
  search: string;
  onSearch: (search: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
  archived: boolean;
  onArchived: (archived: boolean) => void;
  onOpen: (conversation: Conversation) => void;
  onNew: () => void;
  onArchive: (conversation: Conversation) => void;
  onRestore: (conversation: Conversation) => void;
};

/** The left pane: start a conversation, search by title, and the list itself. */
export function ConversationList({
  conversations,
  loaded,
  searchPending,
  loading,
  error,
  onRetry,
  hasMore,
  loadingMore,
  onMore,
  selectedId,
  liveIds,
  now,
  search,
  onSearch,
  searchRef,
  archived,
  onArchived,
  onOpen,
  onNew,
  onArchive,
  onRestore,
}: ConversationListProps) {
  const archivedLabel = useId();
  const searching = search.trim() !== "";

  let body;
  if (loading) {
    body = (
      <p className="px-3 py-6 text-center text-xs text-text-faint" role="status">
        Loading conversations…
      </p>
    );
  } else if (error !== null && loaded === 0) {
    body = (
      <div className="flex flex-col items-center gap-2 px-3 py-6 text-center" role="alert">
        <p className="break-words text-xs text-text-muted">{error}</p>
        <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  } else if (conversations.length === 0) {
    body = (
      <p className="px-3 py-6 text-center text-xs text-text-faint">
        {searching
          ? searchPending
            ? "Searching…"
            : archived
              ? "No archived conversation has a title like that."
              : "No conversation has a title like that."
          : archived
            ? "Nothing is archived."
            : "No conversations yet. Start one with the button above."}
      </p>
    );
  } else {
    body = (
      <ul className="flex flex-col gap-px p-1.5">
        {conversations.map((conversation) => {
          const selected = conversation.id === selectedId;
          const writing = conversation.streaming || liveIds.has(conversation.id);
          const title = conversationTitle(conversation);
          const untitled = conversation.title.trim() === "";
          const daysLeft = archived ? daysUntilDeleted(conversation.deletes_at, now) : null;
          const goes = daysLeft === null || conversation.deletes_at === null ? "" : deletionSentence(conversation.deletes_at, now);
          return (
            <li key={conversation.id} className={`group relative flex items-center rounded-lg ${selected ? "bg-surface-2" : "hover:bg-surface-2/60"}`}>
              <button
                type="button"
                aria-current={selected ? "true" : undefined}
                onClick={() => onOpen(conversation)}
                className="flex min-w-0 flex-1 items-center gap-2 rounded-lg py-1.5 pl-2.5 pr-9 text-left"
              >
                {writing ? (
                  <>
                    <span className="dot chat-writing bg-accent" aria-hidden="true" />
                    <span className="sr-only">A reply is being written.</span>
                  </>
                ) : null}
                <span className={`min-w-0 flex-1 truncate text-[13px] ${untitled ? "italic text-text-faint" : selected ? "font-medium text-text" : "text-text"}`}>{title}</span>
                {conversation.on_record ? null : (
                  <span className="flex-none rounded border border-dashed border-border px-1 font-mono text-[10px] lowercase text-text-faint" title={`${OFF_LABEL}: what is said here now is kept apart from the Tape`}>
                    off<span className="sr-only"> the record</span>
                  </span>
                )}
                {daysLeft === null ? (
                  <span className="flex-none font-mono text-[10px] tabular-nums text-text-faint group-focus-within:invisible group-hover:invisible">
                    {formatListTime(conversation.updated, now)}
                  </span>
                ) : (
                  <span
                    className={`flex-none font-mono text-[10px] tabular-nums group-focus-within:invisible group-hover:invisible ${daysLeft <= 3 ? "text-danger" : "text-text-faint"}`}
                    title={goes}
                  >
                    <span aria-hidden="true">{deletionMark(daysLeft)}</span>
                    <span className="sr-only">{goes}</span>
                  </span>
                )}
              </button>
              <button
                type="button"
                className={ROW_ACTION_CLASS}
                onClick={() => (archived ? onRestore(conversation) : onArchive(conversation))}
                aria-label={archived ? `Restore ${title}` : `Put away ${title}`}
                title={archived ? "Restore" : ARCHIVE_HINT}
              >
                {archived ? <ArchiveRestore size={14} aria-hidden="true" /> : <Archive size={14} aria-hidden="true" />}
              </button>
            </li>
          );
        })}
      </ul>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex flex-none flex-col gap-2 border-b border-border p-2.5">
        <button type="button" className="btn btn-primary w-full" onClick={onNew} title="New conversation (n)">
          <Plus size={14} aria-hidden="true" />
          New conversation
        </button>
        <div className="relative">
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-faint" aria-hidden="true" />
          <input
            ref={searchRef}
            type="search"
            className="input !h-8 !pl-7"
            value={search}
            maxLength={MAX_SEARCH}
            onChange={(event) => onSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape" && search) {
                event.preventDefault();
                onSearch("");
              }
            }}
            placeholder="Search titles"
            aria-label="Search conversations by title"
            title="Search by title (/)"
          />
        </div>
        <div className="flex items-center justify-between gap-2 px-0.5">
          <span id={archivedLabel} className="text-xs text-text-muted">
            Show archived
          </span>
          <Switch checked={archived} onChange={onArchived} labelledBy={archivedLabel} />
        </div>
      </div>
      <nav className="min-h-0 flex-1 overflow-y-auto" aria-label={archived ? "Archived conversations" : "Conversations"}>
        {/* A refresh that failed keeps the conversations already loaded on screen. */}
        {!loading && error !== null && loaded > 0 ? (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-2 text-xs text-text-muted" role="alert">
            <p className="min-w-0 flex-1 basis-32 break-words">This list may be out of date. {error}</p>
            <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={onRetry}>
              Try again
            </button>
          </div>
        ) : null}
        {archived && !loading && conversations.length > 0 ? (
          <p className="border-b border-border px-3 py-2 text-[11px] leading-snug text-text-faint">
            {conversations.some((item) => item.deletes_at !== null && item.deletes_at !== undefined)
              ? ARCHIVED_WILL_GO
              : ARCHIVED_STAYS}
          </p>
        ) : null}
        {body}
        {!loading && (error === null || loaded > 0) && hasMore ? (
          <div className="flex justify-center px-3 pb-3">
            <button type="button" className="btn !h-7 !px-2.5 !text-xs" onClick={onMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </button>
          </div>
        ) : null}
        {!loading && searching && conversations.length > 0 ? (
          <p className="px-3 pb-3 text-center text-[11px] text-text-faint" role="status">
            {searchPending ? "Searching…" : hasMore ? `${conversations.length} found so far` : `${conversations.length} found`}
          </p>
        ) : null}
      </nav>
    </div>
  );
}
