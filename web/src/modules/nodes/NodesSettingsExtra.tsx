import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, Server, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { NODES_KEY, nodesApi } from "./api";
import type { JoinCode, NodeRecord } from "./api";
import { addressNote, affectsNodes, codeLife, joinCommand, nodeStatus, offersLine, removeMessage, removedLine, sortNodes } from "./lib/nodes";

const CARD = "@container rounded-xl border border-border bg-surface";
const TONE_DOT = { ok: "bg-success", warn: "bg-warning", off: "bg-sage" } as const;

/** A refusal will be refused again; only a failure to reach the server is worth another try. */
function retry(count: number, error: unknown): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return count < 2;
}

/** The time now, refreshed often enough for "last heard from" and a code's remaining minutes. */
function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs]);
  return now;
}

function useNodes() {
  const client = useQueryClient();
  // Whether a node is connected is a vital sign, not an event, so it is asked for again every few seconds.
  const query = useQuery({ queryKey: NODES_KEY, queryFn: nodesApi.list, staleTime: 5_000, refetchInterval: 10_000, retry });
  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: NODES_KEY });
    return subscribeFeed({
      onEvent: (event) => {
        if (affectsNodes(event)) refresh();
      },
      onReconnect: refresh,
    });
  }, [client]);
  return query;
}

/** Shown above Settings, Nodes: the machines that have joined, a join code for another, and removing one. Owned by the nodes module. */
export function NodesSettingsExtra() {
  const query = useNodes();
  const client = useQueryClient();
  const toast = useToast();
  const now = useNow(5_000);
  const [code, setCode] = useState<JoinCode | null>(null);
  const [removing, setRemoving] = useState<NodeRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const makeCode = useMutation({
    mutationFn: nodesApi.joinCode,
    onSuccess: (made) => {
      setError(null);
      setCode(made);
    },
    onError: (failure) => setError(errorDetail(failure)),
  });
  const remove = useMutation({
    mutationFn: (node: NodeRecord) => nodesApi.remove(node.id),
    onSuccess: (result) => {
      setRemoving(null);
      setError(null);
      setDone(removedLine(result));
      void client.invalidateQueries({ queryKey: NODES_KEY });
      void client.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (failure) => {
      setRemoving(null);
      setDone(null);
      setError(errorDetail(failure));
    },
  });

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(`${what} copied.`);
    } catch {
      toast.error(`The ${what.toLowerCase()} could not be copied. Select it and copy it by hand.`);
    }
  };

  if (query.isPending) {
    return (
      <p className={`${CARD} px-4 py-4 text-sm text-text-faint`} role="status">
        Loading nodes…
      </p>
    );
  }
  if (!query.data) {
    return (
      <div className={`${CARD} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`} role="alert">
        <p className="min-w-0 flex-1 text-sm text-text-muted">
          <span className="font-medium text-text">Nodes cannot be shown.</span> {errorDetail(query.error)}
        </p>
        <button type="button" className="btn" onClick={() => void query.refetch()} disabled={query.isFetching}>
          Try again
        </button>
      </div>
    );
  }

  const nodes = sortNodes(query.data.nodes);
  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3.5">
        <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
          <Server size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13px] font-semibold text-text">Other machines</h3>
          <p className="text-xs text-text-muted">
            A second machine can lend this one its models. Your agents, your data and every decision stay on this computer. JimmyHQ {query.data.hub_version} runs here.
          </p>
        </div>
        <button type="button" className="btn btn-primary" onClick={() => makeCode.mutate()} disabled={makeCode.isPending}>
          <Plus size={14} aria-hidden="true" />
          {makeCode.isPending ? "Making a code…" : "Add a machine"}
        </button>
      </div>

      {done ? (
        <p className="break-words px-4 py-3 text-xs text-text" role="status">
          {done}
        </p>
      ) : null}
      {error ? (
        <p className="flex items-start gap-2 break-words px-4 py-3 text-xs text-text" role="alert">
          <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
          <span className="min-w-0">{error}</span>
        </p>
      ) : null}

      {nodes.length === 0 ? (
        <p className="px-4 py-4 text-xs text-text-muted">
          No machine has joined. With one computer there is nothing to do here: JimmyHQ works as it always has. To add a second, choose “Add a machine” and run the command it shows on the other machine.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {nodes.map((node) => {
            const said = nodeStatus(node, now);
            return (
              <li key={node.id} className="flex flex-col gap-1.5 px-4 py-3">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium text-text">{node.name}</span>
                  <span className="inline-flex flex-none items-center gap-1.5 text-xs font-medium text-text" role="status">
                    <span className={`dot ${TONE_DOT[said.tone]}`} aria-hidden="true" />
                    {said.label}
                  </span>
                  <button type="button" className="btn" onClick={() => setRemoving(node)}>
                    Remove
                  </button>
                </div>
                <p className="break-words text-xs text-text-muted">
                  {offersLine(node)}
                  {node.version ? ` · JimmyHQ ${node.version}` : ""}
                </p>
                {node.models.some((model) => model.roles.length > 0) ? (
                  <p className="break-words text-xs text-text-faint">
                    {node.models
                      .filter((model) => model.roles.length > 0)
                      .map((model) => `${model.name} is used by ${model.roles.join(", ")}`)
                      .join(" · ")}
                  </p>
                ) : null}
                {said.detail ? (
                  <p className="flex items-start gap-2 break-words text-xs text-text-muted">
                    {said.tone === "warn" ? <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" /> : null}
                    <span className="min-w-0">{said.detail}</span>
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

      <p className="px-4 py-3 text-xs text-text-faint">
        A node’s models appear under Settings, Models as “name on machine” and can be given a role like any other. They count as your own machines. The join code and the node’s token travel over your network as it is: use a private network that encrypts, or https. See docs/nodes.md.
      </p>

      {code ? <JoinCodeDialog code={code} now={now} onCopy={copy} onClose={() => setCode(null)} /> : null}
      {removing ? (
        <ConfirmDialog
          title={`Remove ${removing.name}?`}
          message={removeMessage(removing)}
          confirmLabel="Remove"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate(removing)}
          onCancel={() => setRemoving(null)}
        />
      ) : null}
    </div>
  );
}

function JoinCodeDialog({ code, now, onCopy, onClose }: { code: JoinCode; now: number; onCopy: (text: string, what: string) => Promise<void>; onClose: () => void }) {
  const origin = window.location.origin;
  const command = joinCommand(origin, code.code);
  const note = addressNote(origin);
  const life = codeLife(code.expires_ms, now);
  return (
    <Modal title="Add a machine" onClose={onClose} widthClass="max-w-lg">
      <div className="flex flex-col gap-3 overflow-y-auto px-4 py-4">
        <p className="text-xs text-text-muted">On the other machine, with JimmyHQ installed, run:</p>
        <div className="flex items-center gap-2">
          <code className="min-w-0 flex-1 select-all overflow-x-auto whitespace-nowrap rounded-md border border-border bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{command}</code>
          <button type="button" className="icon-btn" onClick={() => void onCopy(command, "Command")} aria-label="Copy the command" title="Copy">
            <Copy size={14} aria-hidden="true" />
          </button>
        </div>
        <p className="text-xs text-text" role="status">
          The code is <span className="select-all font-mono font-semibold">{code.code}</span>. {life.text}
        </p>
        {note ? (
          <p className="flex items-start gap-2 break-words text-xs text-text-muted">
            <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
            <span className="min-w-0">{note}</span>
          </p>
        ) : null}
        <ol className="flex list-decimal flex-col gap-1.5 pl-4 text-xs text-text-muted">
          <li>
            Then tell it which model server to lend: <code className="font-mono text-text">jimmyhq node offer model --name big --url http://127.0.0.1:PORT/v1</code>
          </li>
          <li>
            Start it: <code className="font-mono text-text">jimmyhq node run</code> (or <code className="font-mono text-text">jimmyhq service install --node</code> to start it at login).
          </li>
          <li>Give its model a role under Settings, Models.</li>
        </ol>
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn btn-primary" onClick={onClose} data-autofocus>
          Done
        </button>
      </div>
    </Modal>
  );
}
