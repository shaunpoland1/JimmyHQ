import { ChevronRight, Download, File, FileText, Folder, FolderOpen } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { formatBytes } from "../../lib/format";
import type { AgentSummary, FileItem } from "./api";
import { useAgents, useFileList, useFileText } from "./hooks";
import { baseName, breadcrumbs, labelledCrumbs, shownName, downloadHref, folderSummary, isMarkdownName, joinPath, looksBinary, parentPath, partialNote, rootLabel, sortItems, zipLabel } from "./lib/files";
import type { Scope } from "./lib/files";
import { displayName, toMs } from "./lib/status";
import { Markdown } from "./Markdown";
import { Note, dateTime } from "./parts";

type Where = { scope: Scope; dir: string; file: string | null };

const SCOPES: { scope: Scope; label: string }[] = [
  { scope: "own", label: "Its own folder" },
  { scope: "shared", label: "Shared folders" },
];

/** The files in the agent's own folder and in the folders every agent can read. Read-only. */
export function FilesTab({ agent }: { agent: AgentSummary }) {
  const name = displayName(agent);
  const [where, setWhere] = useState<Where>({ scope: "own", dir: "", file: null });
  const list = useFileList(where.scope, agent.id, where.dir);
  const label = rootLabel(where.scope, name);
  const everyone = useAgents().data ?? [];
  const trail = labelledCrumbs(where.scope, breadcrumbs(where.file ?? where.dir, label), everyone);
  const items = sortItems(list.data?.items ?? []);

  const goTo = (scope: Scope, dir: string, file: string | null = null) => setWhere({ scope, dir, file });
  const open = (item: FileItem) => (item.dir ? goTo(where.scope, item.path || joinPath(where.dir, item.name)) : goTo(where.scope, where.dir, item.path || joinPath(where.dir, item.name)));

  return (
    <div className="flex flex-col gap-3.5">
      <div className="village-seg" role="group" aria-label="Which files">
        {SCOPES.map((item) => (
          <button key={item.scope} type="button" aria-pressed={where.scope === item.scope} onClick={() => goTo(item.scope, "")}>
            {item.label}
          </button>
        ))}
      </div>
      <p className="text-xs text-text-muted">
        {where.scope === "own"
          ? `What ${name} has made and kept in its own folder. You can read and download it here but not change it.`
          : "What the agents have shared with each other, one folder each, and what retired agents left behind. You can read and download it here but not change it."}
      </p>

      <nav aria-label="Where you are" className="village-crumbs">
        <ol>
          {trail.map((crumb, index) => {
            const last = index === trail.length - 1;
            return (
              <li key={crumb.path || "root"}>
                {index > 0 ? <ChevronRight size={12} aria-hidden="true" /> : null}
                {last ? (
                  <span aria-current="page">{crumb.label}</span>
                ) : (
                  <button type="button" onClick={() => goTo(where.scope, crumb.path)}>
                    {crumb.label}
                  </button>
                )}
              </li>
            );
          })}
        </ol>
      </nav>

      {where.file !== null ? (
        <FileReader scope={where.scope} agentId={agent.id} path={where.file} onBack={() => goTo(where.scope, where.dir)} />
      ) : (
        <section className="village-section" aria-label="Folder contents">
          {list.isPending ? <p className="text-sm text-text-faint">Loading the folder…</p> : null}
          {list.isError ? (
            <div className="flex flex-col items-start gap-2">
              <Note tone="bad">
                {errorDetail(list.error)} {where.dir ? "That folder may have been removed." : "Only the owner can read an agent's files: sign in as the owner, then reload the page."}
              </Note>
              {where.dir ? (
                <button type="button" className="btn h-7 px-2.5 text-xs" onClick={() => goTo(where.scope, parentPath(where.dir))}>
                  Go up a folder
                </button>
              ) : null}
            </div>
          ) : null}
          {list.isSuccess && items.length === 0 ? (
            <p className="text-sm text-text-muted">
              {where.dir
                ? "This folder is empty."
                : where.scope === "own"
                  ? `${name} has not saved any files yet. Files appear here when it writes a report, a note or a draft.`
                  : "Nothing has been shared yet. When an agent shares a file with the others it is listed here, in a folder with its id."}
            </p>
          ) : null}
          {items.length > 0 ? (
            <>
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-text-faint">{folderSummary(items)}</p>
                <a className="btn h-7 px-2.5 text-xs" href={downloadHref(where.scope, agent.id, where.dir, true)} download>
                  <Download size={13} aria-hidden="true" />
                  {zipLabel(where.dir)}
                </a>
              </div>
              <ul className="village-files">
                {items.map((item) => {
                  const shown = shownName(where.scope, where.dir, item.name, everyone);
                  return (
                  <li key={item.path || item.name} className="village-file-row">
                    <button type="button" onClick={() => open(item)} aria-label={item.dir ? `Open folder ${shown}` : `Read ${shown}`}>
                      {item.dir ? <Folder size={15} aria-hidden="true" /> : <File size={15} aria-hidden="true" />}
                      <span className="village-file-name">{shown}</span>
                      <span className="village-file-meta">{item.dir ? "" : typeof item.size === "number" ? formatBytes(item.size) : ""}</span>
                      <span className="village-file-meta">{toMs(item.modified) ? dateTime(toMs(item.modified) as number) : ""}</span>
                    </button>
                    <a
                      className="village-file-download"
                      href={downloadHref(where.scope, agent.id, item.path || joinPath(where.dir, item.name), item.dir)}
                      download
                      aria-label={item.dir ? `Download folder ${shown} as a zip` : `Download ${shown}`}
                      title={item.dir ? "Download as a zip" : "Download"}
                    >
                      <Download size={14} aria-hidden="true" />
                    </a>
                  </li>
                  );
                })}
              </ul>
            </>
          ) : null}
        </section>
      )}
    </div>
  );
}

function FileReader({ scope, agentId, path, onBack }: { scope: Scope; agentId: string; path: string; onBack: () => void }) {
  const file = useFileText(scope, agentId, path);
  const name = baseName(path);
  const markdown = isMarkdownName(name);
  const [raw, setRaw] = useState(false);
  const data = file.data;
  const partial = data ? partialNote(data, formatBytes) : null;
  const binary = data ? looksBinary(data.text) : false;
  const modified = toMs(data?.modified);

  return (
    <section className="village-section" aria-label={`File ${name}`}>
      <h2 className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 normal-case tracking-normal">
          <FileText size={14} aria-hidden="true" />
          <span className="[overflow-wrap:anywhere]">{name}</span>
        </span>
        <span className="flex items-center gap-2 normal-case tracking-normal">
          {markdown && data && !binary ? (
            <button type="button" className="btn h-7 px-2.5 text-xs" onClick={() => setRaw((value) => !value)}>
              {raw ? "Show formatted" : "Show as plain text"}
            </button>
          ) : null}
          <a className="btn h-7 px-2.5 text-xs" href={downloadHref(scope, agentId, path, false)} download>
            <Download size={13} aria-hidden="true" />
            Download
          </a>
          <button type="button" className="btn h-7 px-2.5 text-xs" onClick={onBack}>
            <FolderOpen size={13} aria-hidden="true" />
            Back to the folder
          </button>
        </span>
      </h2>
      {file.isPending ? <p className="text-sm text-text-faint">Reading the file…</p> : null}
      {file.isError ? (
        <Note tone="bad">
          {errorDetail(file.error)} The file may have been moved or removed. Go back to the folder and open it again from the list.
        </Note>
      ) : null}
      {data ? (
        <>
          <p className="mb-2 text-xs text-text-faint">
            {typeof data.size === "number" ? formatBytes(data.size) : "Size not given"}
            {modified ? ` · changed ${dateTime(modified)}` : ""}
            {" · "}
            <span className="font-mono">{data.path || path}</span>
          </p>
          {partial ? (
            <div className="mb-3" role="status">
              <Note tone="warn">{partial}</Note>
            </div>
          ) : null}
          {binary ? (
            <Note>This does not look like a text file, so it is not shown. Open it from the agent's folder on disk to see it.</Note>
          ) : data.text.length === 0 ? (
            <p className="text-sm text-text-muted">This file is empty.</p>
          ) : markdown && !raw ? (
            <Markdown text={data.text} />
          ) : (
            <pre className="village-pre" tabIndex={0}>
              {data.text}
            </pre>
          )}
        </>
      ) : null}
    </section>
  );
}
