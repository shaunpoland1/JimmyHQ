import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { memoryApi } from "./api";
import type { AddFactResult, Fact, Note, Tree } from "./api";
import { TREE_KEY, noteKey, refreshLists, refreshNote, useTree } from "./hooks";
import { keepDraft } from "./lib/drafts";
import { DEFAULT_KIND, describeActor } from "./lib/facts";
import { pathProblem, starterText, takenBy, tidyNewPath } from "./lib/paths";
import { folderChoices } from "./lib/tree";
import { When } from "./parts";

const MAX_FACT = 2000;

/** Why a path cannot be used for a new or moved note, checked against what is already there. */
function problemWith(path: string, tree: Tree | undefined, self?: string): string | null {
  const problem = pathProblem(path);
  if (problem) return problem;
  if (path === self) return "The note is already at that path. Choose a different one.";
  const taken = takenBy(path, (tree?.notes ?? []).map((note) => note.path).filter((other) => other !== self));
  if (taken) return `There is already a note at ${taken}. Choose another name.`;
  const folder = takenBy(path, tree?.folders ?? []);
  if (folder) return `A folder is named ${folder}. Choose another name.`;
  return null;
}

function Buttons({ onCancel, submit, busy, disabled }: { onCancel: () => void; submit: string; busy: boolean; disabled?: boolean }) {
  return (
    <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
      <button type="button" className="btn" onClick={onCancel}>
        Cancel
      </button>
      <button type="submit" className="btn btn-primary" disabled={busy || disabled}>
        {submit}
      </button>
    </div>
  );
}

/** Starts a note. It opens in Edit mode, ready to type in. */
export function NewNoteDialog({ folder, onClose, onCreated }: { folder: string; onClose: () => void; onCreated: (path: string) => void }) {
  const client = useQueryClient();
  const tree = useTree();
  const [typed, setTyped] = useState(folder ? `${folder}/` : "");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const listId = useId();

  const path = tidyNewPath(typed);
  const problem = problemWith(path, tree.data);

  const create = async () => {
    setTouched(true);
    if (problem || busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      // A save with no revision replaces whatever is there, so look once more before writing.
      const fresh = await client.fetchQuery({ queryKey: TREE_KEY, queryFn: memoryApi.tree, staleTime: 0 });
      const clash = problemWith(path, fresh);
      if (clash) {
        setRefusal(clash);
        return;
      }
      const note = await memoryApi.saveNote(path, starterText(path));
      client.setQueryData<Note>(noteKey(note.path), note);
      keepDraft(note.path, { text: note.text, baseText: note.text, revision: note.revision });
      refreshLists(client);
      onCreated(note.path);
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="New note" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <div className="flex flex-col gap-2 px-4 py-4">
          <Field
            label="Where to keep it"
            hint={`A path inside the Library, such as shared/ideas.md. ${path && !problem && path !== typed.trim() ? `It will be saved as ${path}.` : "The name ends in .md."}`}
            error={refusal ?? (touched || typed.trim().length > (folder ? folder.length + 1 : 0) ? (problem ?? undefined) : undefined)}
            value={typed}
            list={listId}
            maxLength={400}
            autoComplete="off"
            spellCheck={false}
            data-autofocus
            onChange={(event) => {
              setTyped(event.target.value);
              setRefusal(null);
            }}
          />
          <datalist id={listId}>{tree.data ? folderChoices(tree.data).map((choice) => <option key={choice} value={`${choice}/`} />) : null}</datalist>
        </div>
        <Buttons onCancel={onClose} submit={busy ? "Creating…" : "Create note"} busy={busy} />
      </form>
    </Modal>
  );
}

/** Renames a note or moves it to another folder. Its facts keep their ids. */
export function MoveNoteDialog({ path, onClose, onMoved }: { path: string; onClose: () => void; onMoved: (note: Note) => void }) {
  const client = useQueryClient();
  const tree = useTree();
  const [typed, setTyped] = useState(path);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  const next = tidyNewPath(typed);
  const problem = next === path ? null : problemWith(next, tree.data, path);

  const move = async () => {
    if (problem || busy || next === path) return;
    setBusy(true);
    setRefusal(null);
    try {
      const note = await memoryApi.moveNote(path, next);
      client.setQueryData<Note>(noteKey(note.path), note);
      client.removeQueries({ queryKey: noteKey(path) });
      refreshLists(client);
      refreshNote(client, note.path);
      onMoved(note);
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="Rename or move" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void move();
        }}
      >
        <div className="px-4 py-4">
          <Field
            label="New path"
            hint="Change the name, or the folders before it to move the note. Its facts and history go with it."
            error={refusal ?? problem ?? undefined}
            value={typed}
            maxLength={400}
            autoComplete="off"
            spellCheck={false}
            data-autofocus
            onChange={(event) => {
              setTyped(event.target.value);
              setRefusal(null);
            }}
          />
        </div>
        <Buttons onCancel={onClose} submit={busy ? "Moving…" : "Move"} busy={busy} disabled={next === path || problem !== null} />
      </form>
    </Modal>
  );
}

type AddFactDialogProps = {
  kinds: readonly string[];
  /** The note that is open, offered as where to put the fact. */
  openPath: string | null;
  onClose: () => void;
  onOpenNote: (path: string) => void;
};

/** Adds a fact. If memory already holds it, the existing fact is shown instead of a second copy. */
export function AddFactDialog({ kinds, openPath, onClose, onOpenNote }: AddFactDialogProps) {
  const client = useQueryClient();
  const tree = useTree();
  const [text, setText] = useState("");
  const [kind, setKind] = useState(kinds.includes(DEFAULT_KIND) ? DEFAULT_KIND : (kinds[0] ?? DEFAULT_KIND));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [result, setResult] = useState<AddFactResult | null>(null);
  const textId = useId();
  const kindId = useId();
  const noteId = useId();

  const add = async () => {
    const typed = text.trim();
    if (!typed || busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      const answer = await memoryApi.addFact(note ? { text: typed, kind, note } : { text: typed, kind });
      refreshLists(client);
      refreshNote(client, answer.fact.note);
      setResult(answer);
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    const fact: Fact = result.fact;
    return (
      <Modal title={result.created ? "Remembered" : "Already remembered"} onClose={onClose}>
        <div className="flex flex-col gap-3 px-4 py-4" role="status">
          <p className="text-[13px] text-text-muted">
            {result.created
              ? "The fact was added and marked as confirmed by you."
              : "Memory already holds a fact that says this, so nothing new was saved. This is the one it has:"}
          </p>
          <p className="rounded-md border border-border bg-bg px-2.5 py-2 text-[13px] text-text [overflow-wrap:anywhere]">{fact.text}</p>
          <p className="text-xs text-text-muted">
            <span className="chip mr-1.5">{fact.kind}</span>
            in <span className="font-mono">{fact.note}</span>, line {fact.line}
            {result.created ? null : (
              <>
                {" "}
                · saved by {describeActor(fact.saved_by)}, <When ts={fact.created_ms} />
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setResult(null);
              setText("");
            }}
          >
            Add another
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              onClose();
              onOpenNote(fact.note);
            }}
          >
            Open its note
          </button>
          <button type="button" className="btn btn-primary" onClick={onClose} data-autofocus>
            Done
          </button>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Add a fact" onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <div className="flex flex-col gap-3 overflow-y-auto px-4 py-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor={textId} className="text-xs font-medium text-text">
              The fact
            </label>
            <textarea
              id={textId}
              className="input h-auto min-h-20 resize-y py-2 leading-snug"
              value={text}
              maxLength={MAX_FACT}
              rows={3}
              placeholder="One statement, such as: Prefers meetings after 10 a.m."
              aria-invalid={refusal ? true : undefined}
              data-autofocus
              onChange={(event) => {
                setText(event.target.value);
                setRefusal(null);
              }}
            />
            {refusal ? (
              <p className="text-xs text-danger" role="alert">
                {refusal}
              </p>
            ) : null}
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex min-w-0 flex-col gap-1.5">
              <label htmlFor={kindId} className="text-xs font-medium text-text">
                Kind
              </label>
              <select id={kindId} className="input" value={kind} onChange={(event) => setKind(event.target.value)}>
                {kinds.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <label htmlFor={noteId} className="text-xs font-medium text-text">
                Note (optional)
              </label>
              <select id={noteId} className="input" value={note} onChange={(event) => setNote(event.target.value)}>
                <option value="">Let the kind decide</option>
                {openPath && !tree.data?.notes.some((item) => item.path === openPath) ? <option value={openPath}>{openPath}</option> : null}
                {tree.data?.notes.map((item) => (
                  <option key={item.path} value={item.path}>
                    {item.path}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <p className="text-xs text-text-faint">It is written as one line in the note, and counts as confirmed because you typed it.</p>
        </div>
        <Buttons onCancel={onClose} submit={busy ? "Adding…" : "Add fact"} busy={busy} disabled={!text.trim()} />
      </form>
    </Modal>
  );
}
