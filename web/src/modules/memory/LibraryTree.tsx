import { ChevronDown, ChevronRight, FilePlus2, FileText, Folder, FolderOpen, LibraryBig, Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState } from "../../components/EmptyState";
import { useTree } from "./hooks";
import { buildTree, filterQuery, filterTree, treeRows } from "./lib/tree";
import { Loading, QueryError } from "./parts";

type LibraryTreeProps = {
  openPath: string | null;
  collapsed: readonly string[];
  onCollapsed: (next: string[]) => void;
  onOpen: (path: string) => void;
  onNewNote: () => void;
};

/** The Library: folders and notes, with how many facts each note holds. */
export function LibraryTree({ openPath, collapsed, onCollapsed, onOpen, onNewNote }: LibraryTreeProps) {
  const tree = useTree();
  const [filter, setFilter] = useState("");
  const filtering = filterQuery(filter) !== "";

  const nodes = useMemo(() => (tree.data ? buildTree(tree.data) : []), [tree.data]);
  const rows = useMemo(() => treeRows(filterTree(nodes, filter), new Set(collapsed), filtering), [nodes, filter, collapsed, filtering]);

  const toggle = (path: string) => onCollapsed(collapsed.includes(path) ? collapsed.filter((item) => item !== path) : [...collapsed, path]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-1.5 border-b border-border px-2.5 py-2">
        <div className="relative min-w-0 flex-1">
          <Search size={13} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-text-faint" aria-hidden="true" />
          <input
            type="search"
            className="input h-7 pl-7 pr-7 text-xs"
            placeholder="Filter notes"
            aria-label="Filter notes by name"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          {filter ? (
            <button type="button" className="icon-btn absolute right-0.5 top-1/2 size-6 -translate-y-1/2" aria-label="Clear the filter" onClick={() => setFilter("")}>
              <X size={12} aria-hidden="true" />
            </button>
          ) : null}
        </div>
        <button type="button" className="icon-btn" aria-label="New note" title="New note (n)" onClick={onNewNote}>
          <FilePlus2 size={15} aria-hidden="true" />
        </button>
      </div>

      <nav aria-label="Library" className="min-h-0 flex-1 overflow-y-auto py-1.5">
        {tree.isError && !tree.data ? (
          <QueryError error={tree.error} onRetry={() => void tree.refetch()} />
        ) : !tree.data ? (
          <Loading what="the Library" />
        ) : rows.length === 0 ? (
          filtering ? (
            <p className="px-3 py-3 text-xs text-text-faint">No note's name contains "{filter.trim()}".</p>
          ) : (
            <EmptyState icon={LibraryBig} title="The Library is empty" description="Start a note and each line that begins with a dash becomes a fact." compact>
              <button type="button" className="btn h-7 px-2.5 text-xs" onClick={onNewNote}>
                New note
              </button>
            </EmptyState>
          )
        ) : (
          <>
            {tree.isError ? <QueryError error={tree.error} onRetry={() => void tree.refetch()} /> : null}
            <ul className="flex flex-col">
              {rows.map(({ node, depth, open }) => {
                const pad = { paddingLeft: `${0.5 + depth * 0.875}rem` };
                if (node.type === "folder") {
                  const Chevron = open ? ChevronDown : ChevronRight;
                  const Icon = open ? FolderOpen : Folder;
                  return (
                    <li key={`d:${node.path}`}>
                      <button
                        type="button"
                        className="flex h-7 w-full items-center gap-1.5 pr-2.5 text-left text-[12.5px] text-text-muted -outline-offset-2 hover:bg-surface-2 hover:text-text"
                        style={pad}
                        aria-expanded={open}
                        aria-label={`Folder ${node.path}, ${node.notes} ${node.notes === 1 ? "note" : "notes"}`}
                        disabled={filtering}
                        onClick={() => toggle(node.path)}
                      >
                        <Chevron size={12} className="flex-none text-text-faint" aria-hidden="true" />
                        <Icon size={13} className="flex-none text-text-faint" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate font-medium">{node.name}</span>
                        {!open && node.facts > 0 ? <span className="flex-none font-mono text-[10.5px] text-text-faint">{node.facts}</span> : null}
                      </button>
                    </li>
                  );
                }
                const current = node.path === openPath;
                return (
                  <li key={`n:${node.path}`}>
                    <button
                      type="button"
                      className={`flex h-7 w-full items-center gap-1.5 pr-2.5 text-left text-[12.5px] -outline-offset-2 ${
                        current ? "bg-surface-2 font-medium text-text shadow-[inset_2px_0_0_var(--accent)]" : "text-text hover:bg-surface-2"
                      }`}
                      style={{ paddingLeft: `${0.5 + depth * 0.875 + 1.125}rem` }}
                      aria-current={current ? "page" : undefined}
                      aria-label={`Note ${node.path}, ${node.facts} ${node.facts === 1 ? "fact" : "facts"}`}
                      title={node.path}
                      onClick={() => onOpen(node.path)}
                    >
                      <FileText size={13} className="flex-none text-text-faint" aria-hidden="true" />
                      <span className="min-w-0 flex-1 truncate">{node.name}</span>
                      <span className="flex-none font-mono text-[10.5px] text-text-faint" aria-hidden="true">
                        {node.facts > 0 ? node.facts : "·"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </nav>
      {tree.data ? (
        <p className="flex-none border-t border-border px-3 py-1.5 font-mono text-[10.5px] text-text-faint">
          {tree.data.notes.length} {tree.data.notes.length === 1 ? "note" : "notes"} · {tree.data.notes.reduce((sum, note) => sum + note.facts, 0)} facts
        </p>
      ) : null}
    </div>
  );
}
