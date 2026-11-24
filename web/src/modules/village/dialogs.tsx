import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { Switch } from "../../components/Switch";
import { villageApi } from "./api";
import type { AgentSummary, Priority } from "./api";
import { AGENTS_KEY, PRIORITIES_KEY, agentKey, useCommand, useVillageProcess } from "./hooks";
import { DEFAULT_BEAT, DEFAULT_NIGHT, MAX_ROLE, NOT_RUNNING, commandProgress, localOnlyProblem, newAgent, parseSeed, roleProblem } from "./lib/commands";
import type { Progress, Target } from "./lib/commands";
import { MAX_DETAILS, MAX_WHY, priorityChange, tidyTitle, titleProblem } from "./lib/priorities";
import { displayName } from "./lib/status";
import { ModelChoiceField, choiceInfo, useAgentModelOptions } from "./ModelChoiceField";
import { DialogButtons, Note } from "./parts";

/** Follows one command and says where it stands. Tells the caller once when it finishes. */
export function useProgress(commandId: string | null, kind: "birth" | "retire" | "rollback", onDone: (agentId: string | null) => void): { progress: Progress | null; agentId: string | null } {
  const { state, error, elapsedMs } = useCommand(commandId);
  const processOk = useVillageProcess(commandId !== null && !state?.done);
  const done = Boolean(state?.done && state.result?.ok !== false);
  const agentId = state?.result?.agent_id ?? state?.agent ?? null;
  useEffect(() => {
    if (done) onDone(agentId);
    // Told once, when the command finishes.
  }, [done]);
  if (commandId === null) return { progress: null, agentId: null };
  if (error && !state) return { progress: { phase: "slow", text: `Could not check on the request: ${errorDetail(error)} It was saved; this dialog keeps trying.` }, agentId: null };
  return { progress: commandProgress({ kind, state, elapsedMs, processOk }), agentId };
}

export function ProgressNote({ progress }: { progress: Progress }) {
  const tone = progress.phase === "done" ? "good" : progress.phase === "refused" ? "bad" : progress.phase === "waiting" ? undefined : "warn";
  return (
    <div aria-live="polite">
      <Note tone={tone}>{progress.text}</Note>
    </div>
  );
}

type CreateProps = {
  /** True when the village already holds as many agents as it may. */
  full: boolean;
  cap: number | null;
  onClose: () => void;
  onMeet: (agentId: string) => void;
};

/** Asks the village for a new agent, then waits with the owner while it is born. */
export function CreateAgentDialog({ full, cap, onClose, onMeet }: CreateProps) {
  const client = useQueryClient();
  const { options, loading } = useAgentModelOptions();
  const processOk = useVillageProcess();
  const [role, setRole] = useState("");
  const [seedText, setSeedText] = useState("");
  const [beat, setBeat] = useState<Target>(DEFAULT_BEAT);
  const [night, setNight] = useState<Target>(DEFAULT_NIGHT);
  const [localOnly, setLocalOnly] = useState(false);
  const [commandId, setCommandId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const localId = useId();

  const { progress, agentId } = useProgress(commandId, "birth", () => void client.invalidateQueries({ queryKey: AGENTS_KEY }));

  const seed = parseSeed(seedText);
  const roleError = roleProblem(role);
  const localWarning = localOnlyProblem(localOnly, choiceInfo(null, beat, options), choiceInfo(null, night, options));

  const create = async () => {
    setTouched(true);
    if (busy || full || roleError || !seed.ok) return;
    setBusy(true);
    setRefusal(null);
    try {
      const ticket = await villageApi.birth(newAgent(role, seed.value, beat, night, localOnly));
      setCommandId(ticket.command_id);
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  if (progress) {
    const finished = progress.phase === "done";
    return (
      <Modal title={finished ? "A new agent has arrived" : progress.phase === "refused" ? "Nobody was born" : "Creating an agent"} onClose={onClose}>
        <div className="flex flex-col gap-3 px-4 py-4 text-sm">
          <ProgressNote progress={progress} />
          {finished ? (
            <p className="text-text-muted">It has no name yet. It will choose one itself over its first few nights; until then it goes by its role.</p>
          ) : progress.phase !== "refused" ? (
            <p className="text-text-muted">You can close this. The agent appears in the Village as soon as it is born.</p>
          ) : null}
        </div>
        <DialogButtons onCancel={onClose} cancel="Close">
          {progress.phase === "refused" ? (
            <button type="button" className="btn btn-primary" onClick={() => setCommandId(null)} data-autofocus>
              Back to the form
            </button>
          ) : null}
          {finished && agentId ? (
            <button type="button" className="btn btn-primary" onClick={() => onMeet(agentId)} data-autofocus>
              Meet them
            </button>
          ) : null}
        </DialogButtons>
      </Modal>
    );
  }

  return (
    <Modal title="Create an agent" onClose={onClose} widthClass="max-w-lg">
      <form
        className="flex min-h-0 flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-4 py-4">
          <div className="village-note">
            <p>
              <b className="font-semibold text-text">What happens.</b> The agent is born blank: no memories and no name. It rolls a temperament once (how fast it tires, how much
              company it needs, what it is curious about) and that never changes. It starts taking turns: look, decide, act. Over its first few nights it
              chooses its own name.
            </p>
          </div>
          {full ? (
            <Note tone="warn">
              The village is full{cap !== null ? `: it holds at most ${cap} agents` : ""}. Retire an agent, or raise "Most agents at once" in Settings, Village, then come back.
            </Note>
          ) : null}
          {processOk === false ? <Note tone="warn">{NOT_RUNNING}</Note> : null}
          <Field
            label="Role"
            hint="A few words on what it is for, such as research or household admin. You can change this later."
            value={role}
            maxLength={MAX_ROLE + 20}
            onChange={(event) => setRole(event.target.value)}
            error={touched ? (roleError ?? undefined) : undefined}
            data-autofocus
          />
          <ModelChoiceField
            label="Everyday model"
            hint="Used for every turn while it is awake. A fast model that runs on your own machine suits this."
            value={beat}
            onChange={setBeat}
            options={options}
            disabled={loading}
          />
          <ModelChoiceField
            label="Night model"
            hint="Used once a night to sort the day into memory. A stronger model does this better."
            value={night}
            onChange={setNight}
            options={options}
            disabled={loading}
          />
          <div className="flex items-start gap-3">
            <Switch checked={localOnly} onChange={setLocalOnly} labelledBy={`${localId}-label`} describedBy={`${localId}-hint`} />
            <div className="min-w-0">
              <p id={`${localId}-label`} className="text-xs font-medium text-text">
                Local only
              </p>
              <p id={`${localId}-hint`} className="text-xs text-text-faint">
                When on, nothing this agent reads or writes is sent to a hosted model, whatever its roles point at.
              </p>
            </div>
          </div>
          {localWarning ? <Note tone="warn">{localWarning}</Note> : null}
          <Field
            label="Seed (optional)"
            hint="The number its temperament is rolled from. Leave blank for a fresh roll; reuse a seed to get the same temperament again."
            value={seedText}
            inputMode="numeric"
            onChange={(event) => setSeedText(event.target.value)}
            error={touched && !seed.ok ? seed.error : undefined}
          />
          {refusal ? <Note tone="bad">{refusal}</Note> : null}
        </div>
        <DialogButtons onCancel={onClose}>
          <button type="submit" className="btn btn-primary" disabled={busy || full}>
            Create agent
          </button>
        </DialogButtons>
      </form>
    </Modal>
  );
}

/** Retires an agent, after saying exactly what is kept and where. */
export function RetireDialog({ agent, onClose, onRetired }: { agent: AgentSummary; onClose: () => void; onRetired: () => void }) {
  const client = useQueryClient();
  const [note, setNote] = useState("");
  const [commandId, setCommandId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const noteId = useId();
  const name = displayName(agent);

  const { progress } = useProgress(commandId, "retire", () => {
    void client.invalidateQueries({ queryKey: AGENTS_KEY });
    void client.invalidateQueries({ queryKey: agentKey(agent.id) });
  });

  const retire = async () => {
    if (busy) return;
    setBusy(true);
    setRefusal(null);
    try {
      const ticket = await villageApi.retire(agent.id, note.trim());
      setCommandId(ticket.command_id);
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  if (progress) {
    const finished = progress.phase === "done";
    return (
      <Modal title={finished ? `${name} has retired` : progress.phase === "refused" ? "Not retired" : `Retiring ${name}`} onClose={finished ? onRetired : onClose}>
        <div className="px-4 py-4 text-sm">
          <ProgressNote progress={progress} />
        </div>
        <DialogButtons onCancel={finished ? onRetired : onClose} cancel={finished ? "Back to the Village" : "Close"} />
      </Modal>
    );
  }

  return (
    <Modal title={`Retire ${name}?`} onClose={onClose} widthClass="max-w-lg">
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-4 text-sm text-text-muted">
        <p>{name} stops for good: no more turns, no more nights. Retiring cannot be undone from this screen.</p>
        <div>
          <p className="mb-1 font-medium text-text">What is kept</p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              Its private folder (diary, memory, files, snapshots) moves to <code className="chip">village/retired/{agent.id}</code> in your data folder. Nothing in it is deleted.
            </li>
            <li>
              What it shared is copied to <code className="chip">village/shared/left/{agent.id}</code>, where the other agents can still read it but not change it.
            </li>
            <li>Its chat with you and everything about it on the Tape stay as they are.</li>
            <li>Its id is never given to another agent.</li>
          </ul>
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={noteId} className="text-xs font-medium text-text">
            A note for the record (optional)
          </label>
          <textarea id={noteId} className="village-textarea" rows={2} maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} placeholder="Why it is retiring" />
        </div>
        {refusal ? <Note tone="bad">{refusal}</Note> : null}
      </div>
      <DialogButtons onCancel={onClose} cancel="Keep it">
        <button type="button" className="btn btn-danger" onClick={() => void retire()} disabled={busy}>
          Retire {name}
        </button>
      </DialogButtons>
    </Modal>
  );
}

/** Adds a standing priority, or edits one. */
export function PriorityDialog({ priority, agents, onClose }: { priority: Priority | null; agents: AgentSummary[]; onClose: () => void }) {
  const client = useQueryClient();
  const [title, setTitle] = useState(priority?.title ?? "");
  const [why, setWhy] = useState(priority?.why ?? "");
  const [details, setDetails] = useState(priority?.details ?? "");
  const [chosen, setChosen] = useState<string[]>(priority?.agents ?? []);
  const [everyone, setEveryone] = useState((priority?.agents.length ?? 0) === 0);
  const [active, setActive] = useState(priority?.active ?? true);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const id = useId();

  const problem = titleProblem(title);
  const nobody = !everyone && chosen.length === 0;
  // Someone the priority already names stays listed, even after retiring, so an edit does not drop them unseen.
  const listed = agents.filter((agent) => !agent.retired || chosen.includes(agent.id));

  const save = async () => {
    setTouched(true);
    if (busy || problem || nobody) return;
    setBusy(true);
    setRefusal(null);
    const next = { title: tidyTitle(title), why: why.trim(), details: details.trim(), agents: everyone ? [] : chosen, active };
    try {
      if (priority) {
        const change = priorityChange(priority, next);
        if (Object.keys(change).length > 0) await villageApi.changePriority(priority.id, change);
      } else {
        await villageApi.addPriority(next);
      }
      void client.invalidateQueries({ queryKey: PRIORITIES_KEY });
      onClose();
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={priority ? "Edit priority" : "Add a priority"} onClose={onClose} widthClass="max-w-lg">
      <form
        className="flex min-h-0 flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-4 py-4">
          <Field label="Title" hint="What matters, in a few words." value={title} onChange={(event) => setTitle(event.target.value)} error={touched ? (problem ?? undefined) : undefined} data-autofocus />
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-why`} className="text-xs font-medium text-text">
              Why it matters
            </label>
            <textarea id={`${id}-why`} className="village-textarea" rows={2} maxLength={MAX_WHY} value={why} onChange={(event) => setWhy(event.target.value)} />
            <p className="text-xs text-text-faint">Agents weigh their own tasks against this, so the reason helps them judge.</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={`${id}-details`} className="text-xs font-medium text-text">
              Details (optional)
            </label>
            <textarea id={`${id}-details`} className="village-textarea" rows={3} maxLength={MAX_DETAILS} value={details} onChange={(event) => setDetails(event.target.value)} />
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1.5 text-xs font-medium text-text">Who it applies to</legend>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" name={`${id}-who`} checked={everyone} onChange={() => setEveryone(true)} />
              Everyone, including agents born later
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" name={`${id}-who`} checked={!everyone} onChange={() => setEveryone(false)} disabled={listed.length === 0} />
              Only these agents
            </label>
            {!everyone ? (
              <div className="ml-6 flex flex-col gap-1.5">
                {listed.map((agent) => (
                  <label key={agent.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={chosen.includes(agent.id)}
                      onChange={(event) => setChosen((current) => (event.target.checked ? [...current, agent.id] : current.filter((item) => item !== agent.id)))}
                    />
                    {displayName(agent)}
                    {agent.retired ? <span className="text-xs text-text-faint">retired</span> : null}
                  </label>
                ))}
                {touched && nobody ? (
                  <p className="text-xs text-danger" role="alert">
                    Tick at least one agent, or choose Everyone.
                  </p>
                ) : null}
              </div>
            ) : null}
          </fieldset>
          <div className="flex items-start gap-3">
            <Switch checked={active} onChange={setActive} labelledBy={`${id}-active`} describedBy={`${id}-active-hint`} />
            <div>
              <p id={`${id}-active`} className="text-xs font-medium text-text">
                Active
              </p>
              <p id={`${id}-active-hint`} className="text-xs text-text-faint">
                A paused priority is kept, but the agents no longer see it.
              </p>
            </div>
          </div>
          {refusal ? <Note tone="bad">{refusal}</Note> : null}
        </div>
        <DialogButtons onCancel={onClose}>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {priority ? "Save changes" : "Add priority"}
          </button>
        </DialogButtons>
      </form>
    </Modal>
  );
}
