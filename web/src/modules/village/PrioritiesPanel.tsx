import { useQueryClient } from "@tanstack/react-query";
import { Pause, Pencil, Play, Plus } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { villageApi } from "./api";
import type { AgentSummary, Priority } from "./api";
import { PriorityDialog } from "./dialogs";
import { PRIORITIES_KEY, usePriorities } from "./hooks";
import { appliesTo, sortPriorities } from "./lib/priorities";
import { displayName } from "./lib/status";
import { Flag, Note } from "./parts";

/** The standing priorities: what matters, to whom, and whether each is switched on. */
export function PrioritiesPanel({ agents }: { agents: AgentSummary[] }) {
  const client = useQueryClient();
  const toast = useToast();
  const priorities = usePriorities();
  const [editing, setEditing] = useState<Priority | "new" | null>(null);
  const [switching, setSwitching] = useState<string | null>(null);

  const setActive = async (priority: Priority, active: boolean) => {
    setSwitching(priority.id);
    try {
      const saved = await villageApi.changePriority(priority.id, { active });
      client.setQueryData<Priority[]>(PRIORITIES_KEY, (current) => current?.map((item) => (item.id === saved.id ? saved : item)));
    } catch (error) {
      toast.error(`${errorDetail(error)} The priority was left as it was.`);
    } finally {
      setSwitching(null);
    }
  };

  const items = sortPriorities(priorities.data ?? []);

  return (
    <section aria-labelledby="village-priorities" className="village-section">
      <h2 id="village-priorities" className="flex items-center justify-between gap-2">
        <span>Priorities</span>
        <button type="button" className="icon-btn" onClick={() => setEditing("new")} aria-label="Add a priority" title="Add a priority">
          <Plus size={15} />
        </button>
      </h2>
      <p className="mb-3 text-xs text-text-muted">What matters most right now. Every agent a priority applies to sees it at each turn and weighs its work against it.</p>
      {priorities.isError ? <Note tone="bad">{errorDetail(priorities.error)} Reload the page to try again.</Note> : null}
      {priorities.isPending ? <p className="text-xs text-text-faint">Loading priorities…</p> : null}
      {priorities.isSuccess && items.length === 0 ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm text-text-muted">No priorities yet. Without one, agents choose their own work.</p>
          <button type="button" className="btn" onClick={() => setEditing("new")}>
            Add a priority
          </button>
        </div>
      ) : null}
      <ul className="flex flex-col gap-2">
        {items.map((priority) => {
          const suggester = priority.suggested_by ? agents.find((agent) => agent.id === priority.suggested_by) : null;
          return (
            <li key={priority.id} className={`rounded-lg border border-border bg-bg px-3 py-2 ${priority.active ? "" : "opacity-70"}`}>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-text [overflow-wrap:anywhere]">{priority.title}</p>
                  {priority.why ? <p className="mt-0.5 text-xs text-text-muted [overflow-wrap:anywhere]">{priority.why}</p> : null}
                </div>
                <button
                  type="button"
                  className="icon-btn"
                  disabled={switching === priority.id}
                  onClick={() => void setActive(priority, !priority.active)}
                  aria-label={priority.active ? `Pause the priority ${priority.title}` : `Resume the priority ${priority.title}`}
                  title={priority.active ? "Pause" : "Resume"}
                >
                  {priority.active ? <Pause size={14} /> : <Play size={14} />}
                </button>
                <button type="button" className="icon-btn" onClick={() => setEditing(priority)} aria-label={`Edit the priority ${priority.title}`} title="Edit">
                  <Pencil size={14} />
                </button>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-text-faint">
                <Flag tone="quiet">For {appliesTo(priority, agents).replace(/^Everyone$/, "everyone")}</Flag>
                {priority.active ? null : <Flag tone="quiet">Paused</Flag>}
                {priority.suggested_by ? <span>Suggested by {suggester ? displayName(suggester) : priority.suggested_by}</span> : null}
              </div>
            </li>
          );
        })}
      </ul>
      {editing ? <PriorityDialog priority={editing === "new" ? null : editing} agents={agents} onClose={() => setEditing(null)} /> : null}
    </section>
  );
}
