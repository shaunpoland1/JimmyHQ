import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Eraser, FileQuestion, FolderInput, History, MoreHorizontal, PanelRight, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import type { Ref } from "react";
import { ApiError, errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState } from "../../components/EmptyState";
import { useToast } from "../../hooks/useToast";
import { formatBytes } from "../../lib/format";
import { memoryApi } from "./api";
import type { Note, VersionSummary } from "./api";
import { MoveNoteDialog } from "./dialogs";
import { useForever } from "./forever";
import { historyKey, noteKey, refreshLists, refreshNote, useHistory, useNote, useVersion } from "./hooks";
import { describeDiff, diffText } from "./lib/diff";
import { draftFor, dropDraft, isDirty, keepDraft } from "./lib/drafts";
import type { Draft } from "./lib/drafts";
import { describeActor, idsLostByEdit } from "./lib/facts";
import { onlyHeadings } from "./lib/markdown";
import { folderOf, nameOf } from "./lib/paths";
import { Label, Loading, NoteMarkdown, QueryError, When } from "./parts";

export type NotePaneHandle = {
  isEditing: () => boolean;
  edit: () => void;
  save: () => void;
  /** Leaves Edit mode, asking first if there are unsaved changes. */
  leaveEdit: () => void;
};

type NotePaneProps = {
  path: string;
  ref?: Ref<NotePaneHandle>;
  /** Back to the Library, below the width where it sits beside the note. */
  onBack: () => void;
  /** Shows the facts and search column, below the width where it sits beside the note. */
  onShowSide: () => void;
  /** The note is gone (deleted, or never there): close it. */
  onClose: () => void;
  onMoved: (path: string) => void;
};

type Conflict = "changed" | "refused" | "gone";

/** The open note: read it, edit it, look through its history. */
export function NotePane({ path, ref, onBack, onShowSide, onClose, onMoved }: NotePaneProps) {
  const client = useQueryClient();
  const toast = useToast();
  const forever = useForever();
  const query = useNote(path);
  const history = useHistory(path);
  const note = query.data;

  // An edit in progress outlives the screen, so coming back finds it as it was left.
  const [draft, setDraftState] = useState<Draft | null>(() => draftFor(path) ?? null);
  const setDraft = useCallback(
    (next: Draft | null) => {
      setDraftState(next);
      if (next) keepDraft(path, next);
      else dropDraft(path);
    },
    [path],
  );
  const [restored] = useState(() => isDirty(draftFor(path)));
  // Edit mode with nothing typed is not worth coming back to.
  useEffect(
    () => () => {
      if (!isDirty(draftFor(path))) dropDraft(path);
    },
    [path],
  );
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<Conflict | null>(null);
  const [showDiff, setShowDiff] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [moving, setMoving] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [versionId, setVersionId] = useState<string | null>(null);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [restoring, setRestoring] = useState(false);

  const editor = useRef<HTMLTextAreaElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);

  const editing = draft !== null;
  const dirty = isDirty(draft);
  // The note on disk is no longer the one this edit started from.
  const behind = draft !== null && note !== undefined && note.revision !== draft.revision;
  const conflict: Conflict | null = refused === "gone" ? "gone" : behind && dirty ? (refused ?? "changed") : null;

  // With nothing typed there is nothing to lose: the editor simply shows the new version.
  useEffect(() => {
    if (draft && note && note.revision !== draft.revision && !isDirty(draft)) {
      setDraft({ text: note.text, baseText: note.text, revision: note.revision });
      setRefused(null);
    }
  }, [draft, note, setDraft]);

  const lost = useMemo(() => (draft && dirty ? idsLostByEdit(draft.baseText, draft.text).length : 0), [draft, dirty]);
  const diff = useMemo(() => (conflict && conflict !== "gone" && draft && note ? diffText(draft.text, note.text) : null), [conflict, draft, note]);

  const edit = useCallback(() => {
    if (!note || draft) return;
    setHistoryOpen(false);
    setVersionId(null);
    setDraft({ text: note.text, baseText: note.text, revision: note.revision });
    requestAnimationFrame(() => editor.current?.focus());
  }, [note, draft, setDraft]);

  const save = useCallback(
    async (anyway = false) => {
      if (!draft || saving) return;
      if (!dirty && !anyway) {
        toast.show("info", "Nothing to save: the note is as it was.");
        return;
      }
      setSaving(true);
      try {
        await client.cancelQueries({ queryKey: noteKey(path) });
        const saved = await memoryApi.saveNote(path, draft.text, anyway ? undefined : draft.revision);
        client.setQueryData<Note>(noteKey(path), saved);
        // Always what the server stored: it gives an id to every newly typed fact.
        setDraft({ text: saved.text, baseText: saved.text, revision: saved.revision });
        setRefused(null);
        setShowDiff(false);
        toast.success("Saved.");
        refreshLists(client);
        void client.invalidateQueries({ queryKey: historyKey(path) });
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          // What was typed stays in the editor; find out what is on disk now.
          try {
            const fresh = await client.fetchQuery({ queryKey: noteKey(path), queryFn: () => memoryApi.note(path), staleTime: 0 });
            setRefused(fresh.revision === draft.revision ? null : "refused");
            if (fresh.revision === draft.revision) toast.error(errorDetail(error));
          } catch (inner) {
            if (inner instanceof ApiError && inner.status === 404) setRefused("gone");
            else toast.error(errorDetail(error));
          }
        } else toast.error(`Not saved. ${errorDetail(error)}`);
      } finally {
        setSaving(false);
      }
    },
    [client, dirty, draft, path, saving, setDraft, toast],
  );

  const leaveEdit = useCallback(() => {
    if (!draft) return;
    if (isDirty(draft)) setConfirmLeave(true);
    else {
      setDraft(null);
      setRefused(null);
    }
  }, [draft, setDraft]);

  useImperativeHandle(ref, () => ({ isEditing: () => editing, edit, save: () => void save(), leaveEdit }), [editing, edit, save, leaveEdit]);

  /** Takes the version on disk into the editor. What was typed can be put back from the toast. */
  const loadNew = () => {
    if (!draft || !note) return;
    const mine = draft.text;
    const fresh: Draft = { text: note.text, baseText: note.text, revision: note.revision };
    setDraft(fresh);
    setRefused(null);
    setShowDiff(false);
    toast.show("info", "Loaded the version on disk.", { label: "Put my text back", run: () => setDraft({ ...fresh, text: mine }) });
  };

  /** Keeps what was typed, on top of the version now on disk: the next save replaces that version. */
  const keepMine = () => {
    if (!draft || !note) return;
    setDraft({ text: draft.text, baseText: note.text, revision: note.revision });
    setRefused(null);
    setShowDiff(false);
  };

  // ---- the menu ----
  useEffect(() => {
    if (!menuOpen) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !menu.current?.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    requestAnimationFrame(() => menu.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus());
    return () => document.removeEventListener("pointerdown", outside);
  }, [menuOpen]);

  const remove = async () => {
    if (!note || deleting) return;
    setDeleting(true);
    try {
      await memoryApi.deleteNote(path);
      const text = note.text;
      client.removeQueries({ queryKey: noteKey(path) });
      refreshLists(client);
      setConfirmDelete(false);
      onClose();
      toast.success(`Deleted ${nameOf(path)}.`, {
        label: "Undo",
        // The same text, ids included, brings the same facts back.
        run: () =>
          void memoryApi
            .saveNote(path, text)
            .then(() => {
              refreshLists(client);
              onMoved(path);
            })
            .catch((error: unknown) => toast.error(`The note was not put back. ${errorDetail(error)}`)),
      });
    } catch (error) {
      toast.error(errorDetail(error));
      setConfirmDelete(false);
    } finally {
      setDeleting(false);
    }
  };

  // ---- history ----
  const versions = history.data?.versions ?? [];
  // A version whose text was erased since it was chosen is no longer shown.
  const chosenGone = versionId !== null && versions.some((item) => item.version === versionId && !item.available);
  const version = useVersion(path, historyOpen && !chosenGone ? versionId : null);
  const latest: VersionSummary | undefined = versions[0];
  const versionDiff = useMemo(() => (version.data && note ? diffText(note.text, version.data.text) : null), [version.data, note]);

  const restore = async () => {
    if (!version.data || !note || restoring) return;
    setRestoring(true);
    try {
      const saved = await memoryApi.saveNote(path, version.data.text, note.revision);
      client.setQueryData<Note>(noteKey(path), saved);
      refreshLists(client);
      refreshNote(client, path);
      setHistoryOpen(false);
      setVersionId(null);
      toast.success("Restored. The version it replaced is in the history.");
    } catch (error) {
      toast.error(`Not restored. ${errorDetail(error)}`);
      refreshNote(client, path);
    } finally {
      setRestoring(false);
      setConfirmRestore(false);
    }
  };

  // ---- drawing ----
  const folder = folderOf(path);
  const header = (
    <header className="flex flex-none flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border px-3 py-2.5 @2xl:px-4">
      <button type="button" className="icon-btn @2xl:hidden" aria-label="Back to the Library" title="Library" onClick={onBack}>
        <ArrowLeft size={16} aria-hidden="true" />
      </button>
      <div className="min-w-0 flex-1 basis-40">
        <h2 className="truncate font-mono text-[13px] font-medium text-text" title={path}>
          {folder ? <span className="text-text-faint">{folder}/</span> : null}
          {nameOf(path)}
        </h2>
        <p className="truncate text-[11.5px] text-text-muted">
          {note ? (
            <>
              Changed <When ts={note.modified_ms} />
              {latest ? <> by {describeActor(latest.actor)}</> : null} · {formatBytes(note.size)} · {note.facts.length} {note.facts.length === 1 ? "fact" : "facts"}
            </>
          ) : (
            " "
          )}
        </p>
      </div>
      <div className="flex flex-none items-center gap-1.5">
        {editing ? (
          <>
            <button type="button" className="btn h-7 px-2.5 text-xs" onClick={leaveEdit} title="Leave Edit mode (Esc)">
              {dirty ? "Discard" : "Done"}
            </button>
            <button type="button" className="btn btn-primary h-7 px-2.5 text-xs" disabled={saving || !dirty || conflict === "gone"} onClick={() => void save()} title="Save (Ctrl or ⌘ S)">
              {saving ? "Saving…" : "Save"}
            </button>
          </>
        ) : note ? (
          <>
            <button type="button" className="btn h-7 px-2.5 text-xs" onClick={edit} title="Edit (e)">
              <Pencil size={13} aria-hidden="true" />
              Edit
            </button>
            <button
              type="button"
              className="icon-btn"
              aria-label={historyOpen ? "Close the history" : "Show this note's history"}
              aria-pressed={historyOpen}
              title="History"
              onClick={() => {
                setHistoryOpen((open) => !open);
                setVersionId(null);
              }}
            >
              <History size={15} aria-hidden="true" />
            </button>
            <div ref={menu} className="relative flex-none">
              <button
                ref={menuButton}
                type="button"
                className="icon-btn"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                aria-label="More for this note"
                title="More"
                onClick={() => setMenuOpen((open) => !open)}
              >
                <MoreHorizontal size={15} aria-hidden="true" />
              </button>
              {menuOpen ? (
                <div
                  role="menu"
                  aria-label="More for this note"
                  className="absolute right-0 top-full z-20 mt-1 flex w-60 max-w-[calc(100vw-2rem)] flex-col rounded-lg border border-border bg-surface p-1.5 shadow-pop"
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.preventDefault();
                      event.stopPropagation();
                      setMenuOpen(false);
                      menuButton.current?.focus();
                      return;
                    }
                    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
                    event.preventDefault();
                    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
                    const at = items.indexOf(document.activeElement as HTMLElement);
                    items[(at + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
                  }}
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-text hover:bg-surface-2"
                    onClick={() => {
                      setMenuOpen(false);
                      setMoving(true);
                    }}
                  >
                    <FolderInput size={14} className="flex-none text-text-muted" aria-hidden="true" />
                    Rename or move…
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-danger hover:bg-surface-2"
                    onClick={() => {
                      setMenuOpen(false);
                      setConfirmDelete(true);
                    }}
                  >
                    <Trash2 size={14} className="flex-none" aria-hidden="true" />
                    Delete…
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] text-danger hover:bg-surface-2"
                    onClick={() => {
                      setMenuOpen(false);
                      forever.deleteNoteForGood(path, note.facts.length, onClose);
                    }}
                  >
                    <Eraser size={14} className="flex-none" aria-hidden="true" />
                    Delete note and its history…
                  </button>
                </div>
              ) : null}
            </div>
          </>
        ) : null}
        <button type="button" className="icon-btn @5xl:hidden" aria-label="Show facts, search and status" title="Facts, search and status" onClick={onShowSide}>
          <PanelRight size={16} aria-hidden="true" />
        </button>
      </div>
    </header>
  );

  let body;
  if (!note && !editing) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    body = query.isError ? (
      <EmptyState
        icon={FileQuestion}
        title={missing ? "This note is not in the Library" : "The note could not be opened"}
        description={errorDetail(query.error)}
        compact
      >
        <div className="flex gap-2">
          <button type="button" className="btn h-7 px-2.5 text-xs" onClick={() => void query.refetch()}>
            Try again
          </button>
          <button type="button" className="btn h-7 px-2.5 text-xs" onClick={onClose}>
            Close it
          </button>
        </div>
      </EmptyState>
    ) : (
      <Loading what="the note" />
    );
  } else if (draft) {
    body = (
      <div className="flex min-h-0 flex-1 flex-col">
        {conflict ? (
          <div className="flex-none border-b border-border bg-surface-2 px-3 py-2.5 text-[12.5px] @2xl:px-4" role={conflict === "changed" ? "status" : "alert"}>
            {conflict === "gone" ? (
              <>
                <p className="text-text">This note was deleted while you were editing. What you typed is still here.</p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <button type="button" className="btn h-7 px-2.5 text-xs" disabled={saving} onClick={() => void save(true)}>
                    Save it as a new note
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="text-text">
                  {conflict === "refused" ? "Not saved: the note changed on disk since you opened it. " : "This note changed on disk while you were editing. "}
                  <span className="text-text-muted">What you typed is still here. {diff ? describeDiff(diff, "The version on disk") : ""}</span>
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  <button type="button" className="btn h-7 px-2.5 text-xs" onClick={loadNew}>
                    Reload it
                  </button>
                  <button type="button" className="btn h-7 px-2.5 text-xs" onClick={keepMine} title="Keep your text. Saving will then replace the version on disk.">
                    Keep editing mine
                  </button>
                  {diff && !diff.same ? (
                    <button type="button" className="btn h-7 px-2.5 text-xs" aria-expanded={showDiff} onClick={() => setShowDiff((open) => !open)}>
                      {showDiff ? "Hide what differs" : "See what differs"}
                    </button>
                  ) : null}
                </div>
                {showDiff && diff ? (
                  <ul className="mt-2 max-h-40 overflow-y-auto rounded-md border border-border bg-surface font-mono text-[11.5px] leading-relaxed" aria-label="Lines that differ">
                    {diff.lines.map((line, index) => (
                      <li key={index} className={`flex gap-2 px-2 ${line.type === "added" ? "text-success" : "text-danger"}`}>
                        <span className="flex-none select-none" aria-hidden="true">
                          {line.type === "added" ? "+" : "−"}
                        </span>
                        <span className="sr-only">{line.type === "added" ? "On disk only:" : "Yours only:"}</span>
                        <span className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{line.text || " "}</span>
                      </li>
                    ))}
                    {diff.more ? <li className="px-2 text-text-faint">…and more</li> : null}
                  </ul>
                ) : null}
              </>
            )}
          </div>
        ) : restored && dirty ? (
          <p className="flex-none border-b border-border bg-surface-2 px-3 py-1.5 text-xs text-text-muted @2xl:px-4" role="status">
            Your unsaved changes from earlier were kept.
          </p>
        ) : null}
        <textarea
          ref={editor}
          data-memory-editor
          className="min-h-0 w-full flex-1 resize-none bg-surface px-3 py-3 font-mono text-[13px] leading-[1.65] text-text -outline-offset-2 @2xl:px-4"
          aria-label={`Markdown of ${path}`}
          value={draft.text}
          spellCheck
          onChange={(event) => setDraft({ ...draft, text: event.target.value })}
        />
        <p className="flex-none border-t border-border px-3 py-1.5 text-[11.5px] leading-snug text-text-faint @2xl:px-4">
          {lost > 0 ? (
            <span className="text-warning">
              Saving will retire {lost} {lost === 1 ? "fact whose line or id mark was removed" : "facts whose lines or id marks were removed"}.{" "}
            </span>
          ) : null}
          A line that starts with a dash is one fact. The <span className="font-mono">{"<!-- m:… -->"}</span> mark at its end is its id: leave it there. New lines get one when you
          save.
        </p>
      </div>
    );
  } else if (note && historyOpen) {
    body = (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex flex-none items-center justify-between gap-2 border-b border-border px-3 py-2 @2xl:px-4">
          <Label>Saved versions</Label>
          <button type="button" className="icon-btn size-6" aria-label="Close the history" onClick={() => setHistoryOpen(false)}>
            <X size={13} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {history.isError ? (
            <QueryError error={history.error} onRetry={() => void history.refetch()} />
          ) : !history.data ? (
            <Loading what="the history" />
          ) : versions.length === 0 ? (
            <p className="px-4 py-4 text-[13px] text-text-faint">No version of this note has been recorded yet.</p>
          ) : (
            <ul className="border-b border-border">
              {versions.map((item, index) => {
                const chosen = item.version === versionId;
                return (
                  <li key={item.version}>
                    <button
                      type="button"
                      className={`flex w-full flex-wrap items-baseline gap-x-2.5 px-3 py-1.5 text-left text-[12.5px] -outline-offset-2 @2xl:px-4 ${
                        chosen ? "bg-surface-2 shadow-[inset_2px_0_0_var(--accent)]" : "hover:bg-surface-2"
                      }`}
                      aria-pressed={chosen}
                      disabled={!item.available}
                      title={item.available ? undefined : "The text of this version is no longer stored, so it cannot be opened or restored."}
                      onClick={() => setVersionId(chosen ? null : item.version)}
                    >
                      <When ts={item.ts} className="font-medium text-text" />
                      <span className="text-text-muted">by {describeActor(item.actor)}</span>
                      <span className="font-mono text-[11px] text-text-faint">
                        {item.facts} {item.facts === 1 ? "fact" : "facts"} · {formatBytes(item.size)}
                      </span>
                      {index === 0 ? <span className="chip">current</span> : null}
                      {item.note !== path ? <span className="min-w-0 truncate font-mono text-[11px] text-text-faint">was {item.note}</span> : null}
                      {!item.available ? <span className="text-[11px] italic text-text-faint">text no longer stored</span> : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {versionId && chosenGone ? (
            <p className="px-4 py-4 text-[13px] text-text-faint">The text of this version is no longer stored, so it cannot be opened or restored.</p>
          ) : versionId ? (
            version.isError ? (
              <QueryError error={version.error} onRetry={() => void version.refetch()} />
            ) : !version.data ? (
              <Loading what="that version" />
            ) : (
              <section aria-label="The chosen version" className="px-3 py-3 @2xl:px-4">
                <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border bg-surface-2 px-3 py-2">
                  <p className="min-w-0 flex-1 basis-48 text-xs text-text-muted">
                    Read only: the note as saved <When ts={version.data.ts} /> by {describeActor(version.data.actor)}.{" "}
                    {versionDiff ? describeDiff(versionDiff, "It", "the note now") : ""}
                  </p>
                  <button type="button" className="btn h-7 px-2.5 text-xs" disabled={restoring || (versionDiff?.same ?? false)} onClick={() => setConfirmRestore(true)}>
                    <RotateCcw size={13} aria-hidden="true" />
                    Restore this version
                  </button>
                </div>
                <NoteMarkdown text={version.data.text} />
              </section>
            )
          ) : versions.length > 0 ? (
            <p className="px-4 py-4 text-[13px] text-text-faint">Choose a version to read it.</p>
          ) : null}
        </div>
      </div>
    );
  } else if (note) {
    body = (
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-4 @2xl:px-6 @2xl:py-5">
        {query.isError ? <QueryError error={query.error} onRetry={() => void query.refetch()} className="mb-3 rounded-lg border border-border !px-3" /> : null}
        <NoteMarkdown text={note.text} />
        {onlyHeadings(note.text) ? (
          <p className="mt-4 max-w-[60ch] text-[13px] text-text-faint">
            Nothing is written here yet. Press Edit and type: each line that starts with a dash becomes one fact.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {header}
      {body}
      {confirmLeave ? (
        <ConfirmDialog
          title="Discard your changes?"
          message={`What you typed in ${nameOf(path)} has not been saved and will be lost.`}
          confirmLabel="Discard"
          cancelLabel="Keep editing"
          danger
          onConfirm={() => {
            setConfirmLeave(false);
            setDraft(null);
            setRefused(null);
          }}
          onCancel={() => {
            setConfirmLeave(false);
            requestAnimationFrame(() => editor.current?.focus());
          }}
        />
      ) : null}
      {confirmDelete ? (
        <ConfirmDialog
          title="Delete this note?"
          message={`${path} is removed from the Library and its ${note?.facts.length ?? 0} ${note?.facts.length === 1 ? "fact is" : "facts are"} retired. Its earlier versions stay in its history and it can be put back. To erase those too, choose "Delete note and its history".`}
          confirmLabel="Delete"
          danger
          busy={deleting}
          onConfirm={() => void remove()}
          onCancel={() => setConfirmDelete(false)}
        />
      ) : null}
      {confirmRestore && version.data ? (
        <ConfirmDialog
          title="Restore this version?"
          message="The note's text is replaced with this version, saved as a new version. The text it has now stays in the history."
          confirmLabel="Restore"
          busy={restoring}
          onConfirm={() => void restore()}
          onCancel={() => setConfirmRestore(false)}
        />
      ) : null}
      {moving ? (
        <MoveNoteDialog
          path={path}
          onClose={() => setMoving(false)}
          onMoved={(moved) => {
            setMoving(false);
            toast.success(`Moved to ${moved.path}.`);
            onMoved(moved.path);
          }}
        />
      ) : null}
    </div>
  );
}
