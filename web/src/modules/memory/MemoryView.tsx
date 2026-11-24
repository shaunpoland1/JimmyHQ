import { BookOpenText, Keyboard } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState } from "../../components/EmptyState";
import { Modal } from "../../components/Modal";
import { useHashRoute } from "../../hooks/useHashRoute";
import { usePersistentState } from "../../hooks/usePersistentState";
import { formatHash } from "../../lib/route";
import { NewNoteDialog } from "./dialogs";
import { ForeverProvider } from "./forever";
import { useMemoryFeed } from "./hooks";
import { dropDraft, hasUnsaved } from "./lib/drafts";
import { KEY_HELP, memoryCommand } from "./lib/keys";
import { folderOf, nameOf, parseMemoryRoute, parseStoredPath } from "./lib/paths";
import { foldersAbove, parseCollapsed } from "./lib/tree";
import { LibraryTree } from "./LibraryTree";
import { NotePane } from "./NotePane";
import type { NotePaneHandle } from "./NotePane";
import { SidePanel } from "./SidePanel";
import type { SidePanelHandle } from "./SidePanel";

const LAST_OPEN_KEY = "jimmyhq.memory.last";
const COLLAPSED_KEY = "jimmyhq.memory.collapsed";
// The @5xl container width (64rem), from which all three columns are shown.
const THREE_COLUMNS_PX = 1024;

/** Which of the three parts is in front when there is not room for all of them. */
type Step = "library" | "note" | "side";

/**
 * Memory: the Library on the left, the open note in the middle, and search,
 * facts and status on the right. Three columns where they fit, then two, then
 * one, a step at a time.
 */
export function MemoryView() {
  // ---- which note is open ----
  const [route, navigate] = useHashRoute();
  // Settings opens over this screen and takes the route; the note stays put behind it.
  const [seen, setSeen] = useState(route);
  const [sub, setSub] = useState<string | null>(route.screen === "memory" ? route.sub : null);
  if (seen !== route) {
    setSeen(route);
    if (route.screen === "memory") setSub(route.sub);
  }
  const wanted = parseMemoryRoute(sub);
  const [shown, setShown] = useState<string | null>(wanted);
  // The note asked for while the one on screen has unsaved changes.
  const [leavingFor, setLeavingFor] = useState<string | null>(null);
  const [lastPath, setLastPath] = usePersistentState<string | null>(LAST_OPEN_KEY, null, parseStoredPath);
  const [collapsed, setCollapsed] = usePersistentState<string[]>(COLLAPSED_KEY, [], parseCollapsed);

  const [step, setStep] = useState<Step>(wanted ? "note" : "library");
  const [newOpen, setNewOpen] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);

  const root = useRef<HTMLDivElement>(null);
  const notePane = useRef<NotePaneHandle>(null);
  const sidePanel = useRef<SidePanelHandle>(null);
  const noteSection = useRef<HTMLElement>(null);

  const onMemory = route.screen === "memory";

  useEffect(() => {
    if (wanted === shown) {
      setLeavingFor(null);
      return;
    }
    if (wanted === null) {
      // Plain #/memory keeps the note that is open.
      if (onMemory && shown) window.location.replace(formatHash({ screen: "memory", sub: shown }));
      return;
    }
    if (hasUnsaved(shown)) {
      setLeavingFor(wanted);
      return;
    }
    setShown(wanted);
  }, [wanted, shown, onMemory]);

  // Plain #/memory with nothing open goes back to the note that was open last.
  useEffect(() => {
    if (!onMemory || wanted !== null || shown !== null || !lastPath) return;
    window.location.replace(formatHash({ screen: "memory", sub: lastPath }));
  }, [onMemory, wanted, shown, lastPath]);

  useEffect(() => {
    if (shown) setLastPath(shown);
  }, [shown, setLastPath]);

  // The folders above the open note are opened, so it can be seen in the Library.
  useEffect(() => {
    if (!shown) return;
    const above = foldersAbove(shown);
    setCollapsed((current) => (current.some((path) => above.includes(path)) ? current.filter((path) => !above.includes(path)) : current));
  }, [shown, setCollapsed]);

  const open = useCallback(
    (path: string) => {
      setStep("note");
      navigate({ screen: "memory", sub: path });
      requestAnimationFrame(() => {
        // Below the three-column width the list that was clicked is hidden now, so focus follows into the note.
        const active = document.activeElement;
        const lost = active === null || active === document.body || (active instanceof HTMLElement && active.offsetParent === null);
        if (lost) noteSection.current?.focus();
      });
    },
    [navigate],
  );

  const close = useCallback(() => {
    setLastPath(null);
    setShown(null);
    setStep("library");
    navigate({ screen: "memory", sub: null });
  }, [navigate, setLastPath]);

  const live = useRef({ shown });
  live.current = { shown };

  useMemoryFeed((from, to) => {
    // The open note was renamed, here or on disk: follow it, unless that would drop typed text.
    if (live.current.shown === from && !hasUnsaved(from) && parseMemoryRoute(to)) navigate({ screen: "memory", sub: to });
  });

  // ---- keys ----
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      const command = memoryCommand({
        // Keys pressed in the sidebar, the top strip or the dock are not this screen's.
        outside: target !== null && target !== document.body && !root.current?.contains(target),
        // A dialog (Settings, the command palette, one of ours) owns the keyboard while it is open.
        dialogOpen: document.querySelector('[role="dialog"]') !== null,
        key: event.key,
        metaKey: event.metaKey,
        ctrlKey: event.ctrlKey,
        altKey: event.altKey,
        shiftKey: event.shiftKey,
        repeat: event.repeat,
        targetTag: target?.tagName,
        editable: target?.isContentEditable,
        inEditor: target?.hasAttribute("data-memory-editor") ?? false,
        editing: notePane.current?.isEditing() ?? false,
        hasNote: live.current.shown !== null,
      });
      if (!command) return;
      event.preventDefault();
      switch (command) {
        case "search":
          // Where the search column is not beside the note, bring it to the front.
          if ((root.current?.clientWidth ?? 0) < THREE_COLUMNS_PX) setStep("side");
          // Wait for the column to be shown before moving focus into it.
          requestAnimationFrame(() => sidePanel.current?.focusSearch());
          break;
        case "newNote":
          setNewOpen(true);
          break;
        case "edit":
          setStep("note");
          requestAnimationFrame(() => notePane.current?.edit());
          break;
        case "save":
          notePane.current?.save();
          break;
        case "leaveEdit":
          notePane.current?.leaveEdit();
          break;
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // ---- drawing ----
  // Library: alone below two columns, always from two columns up.
  const libraryClass = `${step === "library" ? "flex" : "hidden"} @2xl:flex`;
  // Note: with the Library at two columns unless the side column is in front; always at three.
  const noteClass = `${step === "note" ? "flex" : "hidden"} ${step === "side" ? "@2xl:hidden" : "@2xl:flex"} @5xl:flex`;
  // Side column: in front when asked for; always at three.
  const sideClass = `${step === "side" ? "flex" : "hidden"} @5xl:flex`;

  return (
    <ForeverProvider>
      <div ref={root} className="@container flex h-full min-h-0 flex-col">
        <header className="flex flex-none flex-wrap items-center gap-x-2.5 gap-y-1 border-b border-border px-4 py-3">
          <h1 className="text-[17px] font-semibold">Memory</h1>
          <p className="min-w-0 flex-1 truncate text-xs text-text-muted">Plain files on disk: readable, editable, greppable.</p>
          <button type="button" className="icon-btn ml-auto" aria-label="Show keyboard shortcuts" onClick={() => setKeysOpen(true)}>
            <Keyboard size={16} aria-hidden="true" />
          </button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)] @2xl:grid-cols-[minmax(200px,240px)_minmax(0,1fr)] @5xl:grid-cols-[minmax(200px,240px)_minmax(0,1fr)_minmax(300px,360px)]">
          <aside aria-label="Library" className={`${libraryClass} min-h-0 min-w-0 flex-col @2xl:border-r @2xl:border-border`}>
            <LibraryTree openPath={shown} collapsed={collapsed} onCollapsed={setCollapsed} onOpen={open} onNewNote={() => setNewOpen(true)} />
          </aside>

          <section
            ref={noteSection}
            tabIndex={-1}
            aria-label={shown ? `Note ${shown}` : "Note"}
            className={`${noteClass} min-h-0 min-w-0 flex-col bg-surface outline-none`}
          >
            {shown ? (
              <NotePane
                key={shown}
                ref={notePane}
                path={shown}
                onBack={() => setStep("library")}
                onShowSide={() => setStep("side")}
                onClose={close}
                onMoved={(path) => {
                  // The draft, if any, belonged to the old path.
                  if (path !== shown) dropDraft(shown);
                  setShown(path);
                  navigate({ screen: "memory", sub: path });
                }}
              />
            ) : (
              <EmptyState icon={BookOpenText} title="No note is open" description="Choose a note from the Library to read what is remembered, or start a new one." level={2}>
                <div className="flex flex-wrap justify-center gap-2">
                  <button type="button" className="btn @2xl:hidden" onClick={() => setStep("library")}>
                    Open the Library
                  </button>
                  <button type="button" className="btn" onClick={() => setNewOpen(true)}>
                    New note
                  </button>
                  <button type="button" className="btn @5xl:hidden" onClick={() => setStep("side")}>
                    Facts and search
                  </button>
                </div>
              </EmptyState>
            )}
          </section>

          <aside aria-label="Search, facts and status" className={`${sideClass} min-h-0 min-w-0 flex-col @5xl:border-l @5xl:border-border`}>
            <SidePanel ref={sidePanel} openPath={shown} onOpenNote={open} onBack={() => setStep(shown ? "note" : "library")} />
          </aside>
        </div>

        {newOpen ? (
          <NewNoteDialog
            folder={shown ? folderOf(shown) : "shared"}
            onClose={() => setNewOpen(false)}
            onCreated={(path) => {
              setNewOpen(false);
              open(path);
            }}
          />
        ) : null}

        {leavingFor && shown ? (
          <ConfirmDialog
            title="Leave without saving?"
            message={`What you typed in ${nameOf(shown)} has not been saved. Opening ${nameOf(leavingFor)} discards it.`}
            confirmLabel="Discard and open"
            cancelLabel="Keep editing"
            danger
            onConfirm={() => {
              dropDraft(shown);
              setShown(leavingFor);
              setLeavingFor(null);
            }}
            onCancel={() => {
              setLeavingFor(null);
              setStep("note");
              // Put the address back on the note that is still open.
              navigate({ screen: "memory", sub: shown });
            }}
          />
        ) : null}

        {keysOpen ? (
          <Modal title="Memory keys" onClose={() => setKeysOpen(false)} widthClass="max-w-sm">
            <dl className="flex flex-col gap-2 overflow-y-auto px-4 py-4 text-[13px]">
              {KEY_HELP.map((row) => (
                <div key={row.does} className="flex items-baseline gap-3">
                  <dt className="flex w-28 flex-none flex-wrap gap-1">
                    {row.keys.map((key) => (
                      <kbd key={key} className="rounded border border-border bg-bg px-1 font-mono text-[11px] text-text-muted">
                        {key}
                      </kbd>
                    ))}
                  </dt>
                  <dd className="min-w-0 text-text-muted">{row.does}</dd>
                </div>
              ))}
            </dl>
            <p className="border-t border-border px-4 py-3 text-xs text-text-faint">
              Single keys do nothing while you are typing in a field, or while focus is outside Memory.
            </p>
          </Modal>
        ) : null}
      </div>
    </ForeverProvider>
  );
}
