import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, DoorOpen, KeyRound, Plus, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Modal } from "../../components/Modal";
import { Switch } from "../../components/Switch";
import { useToast } from "../../hooks/useToast";
import { subscribeFeed } from "../../lib/feed";
import { formatEventTime } from "../../lib/format";
import { DOOR_KEYS, doorApi } from "./api";
import type { ClientChange, DoorAppTool, DoorAppToolHeldBack, DoorCall, DoorClient, DoorStatus, DoorTool, TokenAnswer } from "./api";
import { tailnetText } from "./lib/tailnet";
import { ABOUT_MAX, APP_TOOLS_NOTE, MESSAGES_NOTE, NAME_MAX, NEVER, affectsDoor, appEffectWords, appGroups, appToolOn, appUsageText, callLine, canDo, checkLimit, checkName, parseAreas, sortClients, statusLine, toolOn, usageText, withLimit, withTool } from "./lib/door";

const CARD = "@container rounded-xl border border-border bg-surface";
const TONE_DOT = { ok: "bg-success", warn: "bg-warning", off: "bg-sage" } as const;

/** Keeps the door's status, clients and receipts current from the live feed. */
function useDoorData() {
  const client = useQueryClient();
  const status = useQuery({ queryKey: DOOR_KEYS.status, queryFn: doorApi.status, staleTime: 15_000 });
  const clients = useQuery({ queryKey: DOOR_KEYS.clients, queryFn: doorApi.clients, staleTime: 15_000 });
  const calls = useQuery({ queryKey: DOOR_KEYS.calls, queryFn: () => doorApi.calls(30), staleTime: 15_000 });

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      clearTimeout(timer);
      // A call prints more than one event: ask once, after they have landed.
      timer = setTimeout(() => void client.invalidateQueries({ queryKey: DOOR_KEYS.all }), 300);
    };
    const stop = subscribeFeed({
      onEvent: (event) => {
        if (affectsDoor(event)) refresh();
      },
      onReconnect: refresh,
    });
    return () => {
      clearTimeout(timer);
      stop();
    };
  }, [client]);

  return { status, clients, calls };
}

/** Shown above the generated settings of the page "Other AI apps" (the MCP door): status and address, the clients, and recent calls. Owned by the door module. */
export function DoorSettingsExtra() {
  const { status, clients, calls } = useDoorData();
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<TokenAnswer | null>(null);

  if (!status.data) {
    if (status.isPending) {
      return (
        <p className={`${CARD} px-4 py-4 text-sm text-text-faint`} role="status">
          Checking the door for other AI apps…
        </p>
      );
    }
    return (
      <div className={`${CARD} flex flex-wrap items-center justify-between gap-3 px-4 py-3.5`} role="alert">
        <p className="min-w-0 flex-1 text-sm text-text-muted">
          <span className="font-medium text-text">The status of the door for other AI apps is unavailable.</span> {errorDetail(status.error)}
        </p>
        <button type="button" className="btn" onClick={() => void status.refetch()} disabled={status.isFetching}>
          Try again
        </button>
      </div>
    );
  }

  const all = sortClients(clients.data ?? []);
  return (
    <div className="flex flex-col gap-4">
      <StatusCard status={status.data} />

      <section className="flex flex-col gap-3" aria-labelledby="door-clients-heading">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <h3 id="door-clients-heading" className="text-[13px] font-semibold text-text">
              Clients
            </h3>
            <p className="text-xs text-text-muted">A client is one AI app or tool you use elsewhere. Each gets its own name, its own token and only the tools you switch on.</p>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
            <Plus size={14} aria-hidden="true" /> Add a client
          </button>
        </div>
        {clients.isError && !clients.data ? (
          <p className={`${CARD} px-4 py-3 text-sm text-text-muted`} role="alert">
            The clients could not be loaded. {errorDetail(clients.error)}
          </p>
        ) : all.length === 0 ? (
          <p className={`${CARD} px-4 py-4 text-sm text-text-muted`}>
            No clients yet. Until you add one and give it a tool, nothing outside can do anything here, even with the door switched on.
          </p>
        ) : (
          all.map((client) => (
            <ClientCard key={client.id} client={client} tools={status.data.tools} appTools={status.data.app_tools ?? []} heldBack={status.data.app_tools_held_back ?? []} onToken={setIssued} />
          ))
        )}
        <p className="text-xs text-text-faint">{MESSAGES_NOTE}</p>
        <p className="text-xs text-text-faint">{NEVER}</p>
      </section>

      <RecentCalls calls={calls.data?.calls ?? []} failed={calls.isError && !calls.data} />

      {adding && (
        <AddClientDialog
          taken={(clients.data ?? []).map((client) => client.name)}
          onClose={() => setAdding(false)}
          onAdded={(answer) => {
            setAdding(false);
            setIssued(answer);
          }}
        />
      )}
      {issued && <TokenDialog answer={issued} onClose={() => setIssued(null)} />}
    </div>
  );
}

function useCopy() {
  const toast = useToast();
  return (text: string, what: string) => {
    navigator.clipboard.writeText(text).then(
      () => toast.success(`${what} copied.`),
      () => toast.error(`${what} could not be copied. Select it and copy it by hand.`),
    );
  };
}

function StatusCard({ status }: { status: DoorStatus }) {
  const copy = useCopy();
  const line = statusLine(status);
  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2 px-4 py-3.5">
        <span className="flex size-8 flex-none items-center justify-center rounded-lg bg-surface-2 text-accent" aria-hidden="true">
          <DoorOpen size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="flex items-center gap-2 text-[13px] font-semibold text-text">
            <span className={`inline-block size-2 rounded-full ${TONE_DOT[line.tone]}`} aria-hidden="true" />
            The door for other AI apps
          </h3>
          <p className="break-words text-xs text-text-muted" role="status">
            {line.text}
          </p>
        </div>
      </div>
      <div className="flex flex-col gap-1.5 px-4 py-3.5">
        <p className="text-xs font-medium text-text">The address to give a client</p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{status.address}</code>
          <button type="button" className="btn" onClick={() => copy(status.address, "The address")}>
            <Copy size={14} aria-hidden="true" /> Copy
          </button>
        </div>
        <p className="text-xs text-text-muted">
          {status.public
            ? `On this computer the door is at ${status.local_address}.`
            : "This address works from this computer only. JimmyHQ never opens the door to the internet: to let a hosted service in, you point your own tunnel at it. See docs/door.md, \"How to expose it\"."}
        </p>
        <TailnetChoice status={status} />
        {status.failed_tries > 0 && (
          <p className="flex items-center gap-1.5 text-xs text-text-muted">
            <TriangleAlert size={13} className="flex-none text-warning" aria-hidden="true" />
            {status.failed_tries} {status.failed_tries === 1 ? "request" : "requests"} with a wrong token in the last 15 minutes.
          </p>
        )}
      </div>
    </div>
  );
}

/** One click between the local address and the one on the owner's Tailscale network. */
function TailnetChoice({ status }: { status: DoorStatus }) {
  const toast = useToast();
  const cache = useQueryClient();
  const tailnet = useQuery({ queryKey: DOOR_KEYS.tailnet, queryFn: doorApi.tailnet, staleTime: 60_000 });
  const change = useMutation({
    mutationFn: doorApi.useAddress,
    onSuccess: (next) => {
      cache.setQueryData(DOOR_KEYS.status, next);
      void cache.invalidateQueries({ queryKey: DOOR_KEYS.tailnet });
      void cache.invalidateQueries({ queryKey: ["settings"] });
      toast.success(next.public ? "Clients are now given your Tailscale address." : "Clients are now given the local address.");
    },
    onError: (error) => toast.error(errorDetail(error)),
  });
  const found = tailnet.data;
  if (!found?.address) return null;
  const text = tailnetText(found, status.public !== null);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border bg-bg px-3 py-2">
      <p className="min-w-0 flex-1 basis-56 text-xs text-text-muted">{text.note}</p>
      <button type="button" className="btn h-7 px-2.5 text-xs" disabled={change.isPending} onClick={() => change.mutate(found.in_use ? "local" : "tailnet")}>
        {text.button}
      </button>
    </div>
  );
}

function ClientCard({
  client,
  tools,
  appTools,
  heldBack,
  onToken,
}: {
  client: DoorClient;
  tools: DoorTool[];
  appTools: DoorAppTool[];
  heldBack: DoorAppToolHeldBack[];
  onToken: (answer: TokenAnswer) => void;
}) {
  const toast = useToast();
  const cache = useQueryClient();
  const [confirm, setConfirm] = useState<"rotate" | "revoke" | null>(null);
  const [open, setOpen] = useState(false);
  const done = () => void cache.invalidateQueries({ queryKey: DOOR_KEYS.all });

  const change = useMutation({
    mutationFn: (body: ClientChange) => doorApi.change(client.id, body),
    onSuccess: done,
    onError: (error) => toast.error(errorDetail(error)),
  });
  const rotate = useMutation({
    mutationFn: () => doorApi.rotate(client.id),
    onSuccess: (answer) => {
      setConfirm(null);
      done();
      onToken(answer);
    },
    onError: (error) => toast.error(errorDetail(error)),
  });
  const revoke = useMutation({
    mutationFn: () => doorApi.revoke(client.id),
    onSuccess: () => {
      setConfirm(null);
      done();
      toast.success(`${client.name} is shut out.`);
    },
    onError: (error) => toast.error(errorDetail(error)),
  });

  if (client.revoked !== null) {
    return (
      <div className={`${CARD} px-4 py-3`}>
        <p className="text-sm text-text-muted">
          <span className="font-mono font-medium text-text">{client.name}</span> was revoked {formatEventTime(client.revoked)}. Its token no longer works and its name is not given to another client.
        </p>
      </div>
    );
  }

  const last = client.last_call;
  return (
    <div className={`${CARD} divide-y divide-border`}>
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3.5">
        <div className="min-w-0 flex-1">
          <h4 className="font-mono text-[13px] font-semibold text-text">{client.name}</h4>
          {client.about && <p className="break-words text-xs text-text-muted">{client.about}</p>}
          <p className="mt-1 text-xs text-text-muted">{canDo(client, tools, appTools)}</p>
          <p className="text-xs text-text-faint">{last ? `Last call ${formatEventTime(last.ts)}: ${callLine(last)}.` : "It has not called yet."}</p>
        </div>
        <div className="flex flex-none flex-wrap gap-2">
          <button type="button" className="btn" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
            {open ? "Hide tools" : "Tools and limits"}
          </button>
          <button type="button" className="btn" onClick={() => setConfirm("rotate")}>
            <KeyRound size={14} aria-hidden="true" /> New token
          </button>
          <button type="button" className="btn btn-danger" onClick={() => setConfirm("revoke")}>
            Revoke
          </button>
        </div>
      </div>
      {open && (
        <>
          <ul className="divide-y divide-border">
            {tools.map((tool) => (
              <ToolRow key={tool.name} client={client} tool={tool} busy={change.isPending} onChange={(next) => change.mutate({ tools: next })} />
            ))}
          </ul>
          <AppTools client={client} tools={appTools} heldBack={heldBack} busy={change.isPending} onChange={(next) => change.mutate({ tools: next })} />
          <ClientOptions client={client} busy={change.isPending} onChange={(options) => change.mutate({ options })} />
        </>
      )}
      {confirm === "rotate" && (
        <ConfirmDialog
          title={`Give ${client.name} a new token?`}
          message="Its current token stops working at once. You will be shown the new one once, and must put it into the client yourself."
          confirmLabel="Make a new token"
          busy={rotate.isPending}
          onConfirm={() => rotate.mutate()}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === "revoke" && (
        <ConfirmDialog
          title={`Revoke ${client.name}?`}
          message="It is shut out on its very next request and cannot be let back in. What it did stays on the record. Its name is not given to another client."
          confirmLabel="Revoke"
          danger
          busy={revoke.isPending}
          onConfirm={() => revoke.mutate()}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  );
}

function ToolRow({ client, tool, busy, onChange }: { client: DoorClient; tool: DoorTool; busy: boolean; onChange: (tools: DoorClient["tools"]) => void }) {
  const toast = useToast();
  const titleId = useId();
  const aboutId = useId();
  const on = toolOn(client, tool);
  const own = client.tools[tool.name];
  const [limit, setLimit] = useState(own == null ? "" : String(own));
  useEffect(() => setLimit(own == null ? "" : String(own)), [own]);

  const saveLimit = () => {
    const check = checkLimit(limit);
    if (!check.ok) {
      toast.error(check.error);
      setLimit(own == null ? "" : String(own));
      return;
    }
    if (check.value !== (own ?? null)) onChange(withLimit(client.tools, tool.name, check.value, tool.always));
  };

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
      <Switch checked={on} disabled={tool.always || busy} labelledBy={titleId} describedBy={aboutId} onChange={(next) => onChange(withTool(client.tools, tool.name, next))} />
      <div className="min-w-0 flex-1 basis-56">
        <p id={titleId} className="text-[13px] font-medium text-text">
          {tool.title} <span className="font-mono text-xs font-normal text-text-faint">{tool.name}</span>
          {tool.write && <span className="chip ml-2 font-sans">adds something</span>}
        </p>
        <p id={aboutId} className="text-xs text-text-muted">
          {tool.description}
          {tool.always ? " Every client has this one." : ""}
        </p>
      </div>
      {on && (
        <label className="flex flex-none items-center gap-2 text-xs text-text-muted">
          <span>{usageText(client, tool)}. Its own limit:</span>
          <input
            className="input w-24"
            inputMode="numeric"
            value={limit}
            placeholder={String(tool.per_day)}
            aria-label={`Calls a day for ${tool.title}; empty for the usual ${tool.per_day}`}
            onChange={(event) => setLimit(event.target.value)}
            onBlur={saveLimit}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
        </label>
      )}
    </li>
  );
}

/** The tools of the owner's installed apps, one group per app, each off until switched on for this client. */
function AppTools({ client, tools, heldBack, busy, onChange }: { client: DoorClient; tools: DoorAppTool[]; heldBack: DoorAppToolHeldBack[]; busy: boolean; onChange: (tools: DoorClient["tools"]) => void }) {
  if (!tools.length && !heldBack.length) return null;
  return (
    <div className="flex flex-col gap-2 px-4 py-3.5">
      <p className="text-xs font-medium text-text">From your apps</p>
      <p className="text-xs text-text-muted">{APP_TOOLS_NOTE}</p>
      {appGroups(tools).map((group) => (
        <section key={group.app_id} aria-label={`Tools from ${group.app_name}`} className="rounded-lg border border-border">
          <h5 className="border-b border-border px-3 py-2 text-xs font-semibold text-text">{group.app_name}</h5>
          <ul className="divide-y divide-border">
            {group.tools.map((tool) => (
              <AppToolRow key={tool.key} client={client} tool={tool} busy={busy} onChange={onChange} />
            ))}
          </ul>
        </section>
      ))}
      {heldBack.length > 0 && (
        <details className="text-xs text-text-muted">
          <summary className="cursor-pointer">
            {heldBack.length} {heldBack.length === 1 ? "tool" : "tools"} of your apps {heldBack.length === 1 ? "is" : "are"} never offered to outside clients
          </summary>
          <ul className="mt-1 flex flex-col gap-1">
            {heldBack.map((tool) => (
              <li key={`${tool.app_id}:${tool.tool}`}>
                <span className="font-medium text-text">{tool.app_name}</span> <span className="font-mono">{tool.name}</span>: {tool.why}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function AppToolRow({ client, tool, busy, onChange }: { client: DoorClient; tool: DoorAppTool; busy: boolean; onChange: (tools: DoorClient["tools"]) => void }) {
  const toast = useToast();
  const titleId = useId();
  const aboutId = useId();
  const on = appToolOn(client, tool);
  const own = client.tools[tool.key];
  const [limit, setLimit] = useState(own == null ? "" : String(own));
  useEffect(() => setLimit(own == null ? "" : String(own)), [own]);

  const saveLimit = () => {
    const check = checkLimit(limit);
    if (!check.ok) {
      toast.error(check.error);
      setLimit(own == null ? "" : String(own));
      return;
    }
    if (check.value !== (own ?? null)) onChange(withLimit(client.tools, tool.key, check.value));
  };

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
      <Switch checked={on} disabled={busy} labelledBy={titleId} describedBy={aboutId} onChange={(next) => onChange(withTool(client.tools, tool.key, next))} />
      <div className="min-w-0 flex-1 basis-56">
        <p id={titleId} className="text-[13px] font-medium text-text">
          {tool.title} <span className="font-mono text-xs font-normal text-text-faint">{tool.name}</span>
          <span className="chip ml-2 font-sans">{appEffectWords(tool)}</span>
        </p>
        <p id={aboutId} className="break-words text-xs text-text-muted">
          {tool.description}
        </p>
      </div>
      {on && (
        <label className="flex flex-none items-center gap-2 text-xs text-text-muted">
          <span>{appUsageText(client, tool)}. Its own limit:</span>
          <input
            className="input w-24"
            inputMode="numeric"
            value={limit}
            placeholder={String(tool.per_day)}
            aria-label={`Calls a day for ${tool.title}; empty for the usual ${tool.per_day}`}
            onChange={(event) => setLimit(event.target.value)}
            onBlur={saveLimit}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
            }}
          />
        </label>
      )}
    </li>
  );
}

function ClientOptions({ client, busy, onChange }: { client: DoorClient; busy: boolean; onChange: (options: Partial<DoorClient["options"]>) => void }) {
  const bodiesId = useId();
  const addressesId = useId();
  const approvalId = useId();
  const [areas, setAreas] = useState(client.options.task_areas.join(", "));
  useEffect(() => setAreas(client.options.task_areas.join(", ")), [client.options.task_areas]);

  const saveAreas = () => {
    const next = parseAreas(areas);
    if (next.join(",") !== client.options.task_areas.join(",")) onChange({ task_areas: next });
  };

  return (
    <div className="flex flex-col gap-3 px-4 py-3.5">
      <p className="text-xs font-medium text-text">What this client is shown, and how its messages arrive</p>
      <div className="flex items-start gap-3">
        <Switch checked={client.options.bench_bodies} disabled={busy} labelledBy={bodiesId} onChange={(next) => onChange({ bench_bodies: next })} />
        <p id={bodiesId} className="min-w-0 flex-1 text-xs text-text-muted">
          <span className="font-medium text-text">Card summaries and bodies.</span> Off, it sees only the titles and kinds of the cards on your Bench.
        </p>
      </div>
      <div className="flex items-start gap-3">
        <Switch checked={client.options.addresses} disabled={busy} labelledBy={addressesId} onChange={(next) => onChange({ addresses: next })} />
        <p id={addressesId} className="min-w-0 flex-1 text-xs text-text-muted">
          <span className="font-medium text-text">Web and mail addresses.</span> Off, addresses in anything it reads are replaced with a marker. Anything that looks like a password or a key is always taken out.
        </p>
      </div>
      <div className="flex items-start gap-3">
        <Switch checked={client.options.messages_need_approval ?? false} disabled={busy} labelledBy={approvalId} onChange={(next) => onChange({ messages_need_approval: next })} />
        <p id={approvalId} className="min-w-0 flex-1 text-xs text-text-muted">
          <span className="font-medium text-text">You read its messages to agents first.</span> On, each message it sends an agent becomes a card on your Bench, with the exact text. The agent gets it only if you approve, exactly as written; a message you decline is never delivered. Off, a message goes straight to an agent that takes outside messages.
        </p>
      </div>
      <label className="flex flex-col gap-1 text-xs text-text-muted">
        <span>
          <span className="font-medium text-text">Task areas.</span> Separate them with commas. Empty means every area.
        </span>
        <input
          className="input"
          value={areas}
          placeholder="every area"
          onChange={(event) => setAreas(event.target.value)}
          onBlur={saveAreas}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
          }}
        />
      </label>
    </div>
  );
}

function AddClientDialog({ taken, onClose, onAdded }: { taken: string[]; onClose: () => void; onAdded: (answer: TokenAnswer) => void }) {
  const cache = useQueryClient();
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const add = useMutation({
    mutationFn: (clean: string) => doorApi.add(clean, about.trim()),
    onSuccess: (answer) => {
      void cache.invalidateQueries({ queryKey: DOOR_KEYS.all });
      onAdded(answer);
    },
    onError: (failure) => setError(errorDetail(failure)),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const check = checkName(name, taken);
    if (!check.ok) {
      setError(check.error);
      return;
    }
    setError(null);
    add.mutate(check.value);
  };

  return (
    <Modal title="Add a client" onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col">
        <div className="flex flex-col gap-3 px-4 py-4">
          <p className="text-sm text-text-muted">A client is one outside system. It starts with no tools: you switch them on afterwards, one by one.</p>
          <label className="flex flex-col gap-1 text-xs font-medium text-text">
            Name
            <input
              className="input font-mono"
              value={name}
              maxLength={NAME_MAX}
              placeholder="helper"
              data-autofocus
              aria-invalid={error ? "true" : undefined}
              aria-describedby={error ? errorId : undefined}
              onChange={(event) => setName(event.target.value)}
            />
            <span className="font-normal text-text-muted">Everything it does is signed with this name. It cannot be changed or reused.</span>
          </label>
          <label className="flex flex-col gap-1 text-xs font-medium text-text">
            What it is
            <input className="input" value={about} maxLength={ABOUT_MAX} placeholder="An assistant on another machine" onChange={(event) => setAbout(event.target.value)} />
          </label>
          {error && (
            <p id={errorId} className="text-xs text-danger" role="alert">
              {error}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={add.isPending}>
            Add and show its token
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** The one time a token is on screen. It is held in this dialog's state only. */
function TokenDialog({ answer, onClose }: { answer: TokenAnswer; onClose: () => void }) {
  const copy = useCopy();
  return (
    <Modal title={`The token for ${answer.client.name}`} onClose={onClose} widthClass="max-w-lg">
      <div className="flex flex-col gap-3 px-4 py-4">
        <p className="text-sm text-text-muted">
          <span className="font-medium text-text">This is shown once.</span> JimmyHQ does not keep it and cannot show it again. Put it into the client now, as a bearer token (it may be called an API key or an Authorization header).
        </p>
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-text">Token</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{answer.token}</code>
            <button type="button" className="btn" onClick={() => copy(answer.token, "The token")} data-autofocus>
              <Copy size={14} aria-hidden="true" /> Copy
            </button>
          </div>
        </div>
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-text">Address</p>
          <div className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-xs text-text">{answer.address}</code>
            <button type="button" className="btn" onClick={() => copy(answer.address, "The address")}>
              <Copy size={14} aria-hidden="true" /> Copy
            </button>
          </div>
        </div>
        <p className="text-xs text-text-muted">Anyone who has this token can do what this client can do. If it is ever seen by someone else, give the client a new token.</p>
      </div>
      <div className="flex justify-end border-t border-border px-4 py-3">
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have saved it
        </button>
      </div>
    </Modal>
  );
}

function RecentCalls({ calls, failed }: { calls: DoorCall[]; failed: boolean }) {
  return (
    <section className={`${CARD} divide-y divide-border`} aria-labelledby="door-calls-heading">
      <div className="px-4 py-3">
        <h3 id="door-calls-heading" className="text-[13px] font-semibold text-text">
          Recent calls
        </h3>
        <p className="text-xs text-text-muted">Every call is recorded: who, which tool and how it went. What a client asked for in words is not kept here.</p>
      </div>
      {failed ? (
        <p className="px-4 py-3 text-sm text-text-muted" role="alert">
          The recent calls could not be loaded.
        </p>
      ) : calls.length === 0 ? (
        <p className="px-4 py-3 text-sm text-text-muted">No calls yet.</p>
      ) : (
        <ul className="max-h-72 divide-y divide-border overflow-y-auto">
          {calls.map((call) => (
            <li key={call.seq} className="flex items-baseline gap-3 px-4 py-2 text-xs">
              <span className={`inline-block size-1.5 flex-none translate-y-[-1px] rounded-full ${call.ok ? "bg-success" : "bg-warning"}`} aria-hidden="true" />
              <span className="min-w-0 flex-1 break-words text-text">{callLine(call)}</span>
              <time className="flex-none text-text-faint" dateTime={new Date(call.ts).toISOString()}>
                {formatEventTime(call.ts)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
