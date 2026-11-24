// The Library as a tree, built from the flat list of folders and notes the
// server sends.

import type { Tree, TreeNote } from "../api.ts";

export type FolderNode = {
  type: "folder";
  /** Full path of the folder, e.g. "agents/scout". */
  path: string;
  name: string;
  children: TreeNode[];
  /** Notes anywhere under it. */
  notes: number;
  /** Facts anywhere under it. */
  facts: number;
};
export type NoteNode = { type: "note"; path: string; name: string; facts: number; size: number; modified_ms: number };
export type TreeNode = FolderNode | NoteNode;

export type TreeRow = { node: TreeNode; depth: number; open: boolean };

function byName(a: TreeNode, b: TreeNode): number {
  // Folders first, then names without regard to case, as a file browser lists them.
  if (a.type !== b.type) return a.type === "folder" ? -1 : 1;
  const one = a.name.toLowerCase();
  const two = b.name.toLowerCase();
  return one < two ? -1 : one > two ? 1 : a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
}

function finish(nodes: TreeNode[]): { notes: number; facts: number } {
  nodes.sort(byName);
  let notes = 0;
  let facts = 0;
  for (const node of nodes) {
    if (node.type === "note") {
      notes += 1;
      facts += node.facts;
    } else {
      const inside = finish(node.children);
      node.notes = inside.notes;
      node.facts = inside.facts;
      notes += inside.notes;
      facts += inside.facts;
    }
  }
  return { notes, facts };
}

/** Folders and notes nested, folders first. A folder listed with no notes is kept: it may be new. */
export function buildTree(tree: Tree): TreeNode[] {
  const roots: TreeNode[] = [];
  const folders = new Map<string, FolderNode>();

  const folder = (path: string): FolderNode => {
    const known = folders.get(path);
    if (known) return known;
    const at = path.lastIndexOf("/");
    const node: FolderNode = { type: "folder", path, name: path.slice(at + 1), children: [], notes: 0, facts: 0 };
    folders.set(path, node);
    (at < 0 ? roots : folder(path.slice(0, at)).children).push(node);
    return node;
  };

  for (const path of tree.folders) if (path) folder(path);
  for (const note of tree.notes) {
    const at = note.path.lastIndexOf("/");
    const node: NoteNode = { type: "note", path: note.path, name: note.path.slice(at + 1), facts: note.facts, size: note.size, modified_ms: note.modified_ms };
    (at < 0 ? roots : folder(note.path.slice(0, at)).children).push(node);
  }
  finish(roots);
  return roots;
}

/** What is typed in the filter box, ready to compare. */
export function filterQuery(typed: string): string {
  return typed.trim().toLowerCase();
}

/** Only the notes whose path contains the words typed, and the folders that hold them. */
export function filterTree(nodes: readonly TreeNode[], typed: string): TreeNode[] {
  const query = filterQuery(typed);
  if (!query) return [...nodes];
  const keep = (list: readonly TreeNode[]): TreeNode[] => {
    const out: TreeNode[] = [];
    for (const node of list) {
      if (node.type === "note") {
        if (node.path.toLowerCase().includes(query)) out.push(node);
        continue;
      }
      const children = keep(node.children);
      if (children.length === 0) continue;
      const copy: FolderNode = { ...node, children, notes: 0, facts: 0 };
      out.push(copy);
    }
    return out;
  };
  const kept = keep(nodes);
  finish(kept);
  return kept;
}

/**
 * The rows to draw, top to bottom. A collapsed folder hides what is under it;
 * `allOpen` (while filtering) shows every match whatever is collapsed.
 */
export function treeRows(nodes: readonly TreeNode[], collapsed: ReadonlySet<string>, allOpen = false): TreeRow[] {
  const rows: TreeRow[] = [];
  const walk = (list: readonly TreeNode[], depth: number) => {
    for (const node of list) {
      const open = node.type === "folder" && (allOpen || !collapsed.has(node.path));
      rows.push({ node, depth, open });
      if (node.type === "folder" && open) walk(node.children, depth + 1);
    }
  };
  walk(nodes, 0);
  return rows;
}

/** Every folder that has to be open for a note to be seen. */
export function foldersAbove(path: string): string[] {
  const parts = path.split("/").slice(0, -1);
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

/** The collapsed folders read back from storage. */
export function parseCollapsed(stored: unknown): string[] | undefined {
  return Array.isArray(stored) && stored.every((item) => typeof item === "string") ? (stored as string[]) : undefined;
}

/** The folders a new note could go in, for the dialog's suggestions. */
export function folderChoices(tree: Tree): string[] {
  const all = new Set(tree.folders);
  for (const note of tree.notes) for (const folder of foldersAbove(note.path)) all.add(folder);
  return [...all].sort();
}

export function findNote(tree: Tree | undefined, path: string | null): TreeNote | undefined {
  return path === null ? undefined : tree?.notes.find((note) => note.path === path);
}
