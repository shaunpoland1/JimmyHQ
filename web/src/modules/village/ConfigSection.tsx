import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Switch } from "../../components/Switch";
import { formatHash } from "../../lib/route";
import { villageApi } from "./api";
import type { AgentCard, AgentConfig } from "./api";
import { AGENTS_KEY, agentKey, useCapabilities } from "./hooks";
import { DEFAULT_BEAT, DEFAULT_NIGHT, MAX_ROLE, capabilityLabel, capabilityRows, localOnlyProblem, roleProblem, sameConfig, toChoice, toTarget, toggleCapability } from "./lib/commands";
import type { Target } from "./lib/commands";
import { ChoiceLine, ModelChoiceField, choiceInfo, useAgentModelOptions } from "./ModelChoiceField";
import { Note, Section } from "./parts";

const BLANK: AgentConfig = { role: "", beat: { role: "default" }, night: { role: "smart" }, local_only: false, capabilities: [] };

/** How an agent is set up: its role, its two models, and what it may use. Read first, edited on request. */
export function ConfigSection({ agent }: { agent: AgentCard }) {
  const client = useQueryClient();
  const { options, loading } = useAgentModelOptions();
  const config = { ...BLANK, ...(agent.config ?? {}), role: agent.config?.role ?? agent.role ?? "" };
  const [editing, setEditing] = useState(false);
  const [role, setRole] = useState("");
  const [beat, setBeat] = useState<Target>(DEFAULT_BEAT);
  const [night, setNight] = useState<Target>(DEFAULT_NIGHT);
  const [localOnly, setLocalOnly] = useState(false);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  const offered = useCapabilities();
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const id = useId();

  const beatInfo = choiceInfo(config.beat, DEFAULT_BEAT, options);
  const nightInfo = choiceInfo(config.night, DEFAULT_NIGHT, options);

  const start = () => {
    setRole(config.role);
    setBeat(toTarget(config.beat, DEFAULT_BEAT));
    setNight(toTarget(config.night, DEFAULT_NIGHT));
    setLocalOnly(config.local_only);
    setCapabilities([...config.capabilities]);
    setRefusal(null);
    setEditing(true);
  };

  if (!editing) {
    const warning = localOnlyProblem(config.local_only, beatInfo, nightInfo);
    return (
      <Section
        title="Setup"
        action={
          agent.retired ? null : (
            <button type="button" className="text-xs normal-case tracking-normal text-accent underline underline-offset-2" onClick={start}>
              Edit
            </button>
          )
        }
      >
        <dl className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
          <dt className="text-text-muted">Role</dt>
          <dd className="[overflow-wrap:anywhere]">{config.role || <span className="text-text-faint">None set</span>}</dd>
          <dt className="text-text-muted">Everyday model</dt>
          <dd className="[overflow-wrap:anywhere]">
            <ChoiceLine info={beatInfo} />
          </dd>
          <dt className="text-text-muted">Night model</dt>
          <dd className="[overflow-wrap:anywhere]">
            <ChoiceLine info={nightInfo} />
          </dd>
          <dt className="text-text-muted">Local only</dt>
          <dd>{config.local_only ? "On: nothing goes to a hosted model" : "Off"}</dd>
          <dt className="text-text-muted">May use</dt>
          <dd className="flex flex-wrap gap-1">
            {config.capabilities.length === 0 ? <span className="text-text-faint">Nothing beyond the basics</span> : null}
            {config.capabilities.map((item) => (
              <span key={item} className="chip">
                {capabilityLabel(offered.data, item)}
              </span>
            ))}
          </dd>
        </dl>
        {warning ? (
          <div className="mt-3">
            <Note tone="warn">{warning}</Note>
          </div>
        ) : null}
      </Section>
    );
  }

  const roleError = roleProblem(role);
  const rows = capabilityRows(offered.data, capabilities);
  const next: AgentConfig = { role: role.trim(), beat: toChoice(beat), night: toChoice(night), local_only: localOnly, capabilities };
  const unchanged = sameConfig(config, next);
  const warning = localOnlyProblem(localOnly, choiceInfo(null, beat, options), choiceInfo(null, night, options));

  const save = async () => {
    if (busy || roleError) return;
    setBusy(true);
    setRefusal(null);
    try {
      const saved = await villageApi.configure(agent.id, next);
      client.setQueryData<AgentCard>(agentKey(agent.id), (current) => (current ? { ...current, config: saved, role: saved.role } : current));
      void client.invalidateQueries({ queryKey: AGENTS_KEY });
      setEditing(false);
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Setup">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Field label="Role" hint="A few words on what it is for." value={role} maxLength={MAX_ROLE + 20} onChange={(event) => setRole(event.target.value)} error={roleError ?? undefined} />
        <ModelChoiceField label="Everyday model" hint="Used for every turn while it is awake." value={beat} onChange={setBeat} options={options} disabled={loading} />
        <ModelChoiceField label="Night model" hint="Used once a night to sort the day into memory." value={night} onChange={setNight} options={options} disabled={loading} />
        <div className="flex items-start gap-3">
          <Switch checked={localOnly} onChange={setLocalOnly} labelledBy={`${id}-local`} describedBy={`${id}-local-hint`} />
          <div className="min-w-0">
            <p id={`${id}-local`} className="text-xs font-medium text-text">
              Local only
            </p>
            <p id={`${id}-local-hint`} className="text-xs text-text-faint">
              When on, nothing this agent reads or writes is sent to a hosted model.
            </p>
          </div>
        </div>
        {warning ? <Note tone="warn">{warning}</Note> : null}
        <div className="flex flex-col gap-3" role="group" aria-labelledby={`${id}-caps`}>
          <p id={`${id}-caps`} className="text-xs font-medium text-text">
            May use
          </p>
          {offered.isError ? <Note tone="warn">What an agent may use could not be loaded. {errorDetail(offered.error)}</Note> : null}
          {rows.length === 0 && !offered.isError ? (
            <p className="text-xs text-text-faint">{offered.isPending ? "Loading…" : "Nothing beyond the basics is available yet."}</p>
          ) : null}
          {rows.map((row) => (
            <div key={row.id} className="flex items-start gap-3">
              <Switch
                checked={row.on}
                onChange={(on) => setCapabilities((held) => toggleCapability(held, row.id, on))}
                labelledBy={`${id}-cap-${row.id}`}
                describedBy={`${id}-cap-${row.id}-hint`}
              />
              <div className="min-w-0">
                <p id={`${id}-cap-${row.id}`} className="text-xs font-medium text-text [overflow-wrap:anywhere]">
                  {row.label}
                </p>
                <p id={`${id}-cap-${row.id}-hint`} className="text-xs text-text-faint">
                  {row.description}
                </p>
                {row.problem ? (
                  <p className="mt-1 text-xs text-text-muted">
                    {row.problem}{" "}
                    {row.settings ? (
                      <a href={formatHash({ screen: "settings", sub: row.settings })} className="underline underline-offset-2 hover:text-text">
                        Open settings
                      </a>
                    ) : null}
                  </p>
                ) : null}
              </div>
            </div>
          ))}
        </div>
        {refusal ? <Note tone="bad">{refusal}</Note> : null}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={() => setEditing(false)}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || unchanged}>
            Save setup
          </button>
        </div>
      </form>
    </Section>
  );
}
