// An app's roles: the step in the install dialog, and the Roles panel on the app's page.
// The owner reads each role's purpose, tools and charter, then creates an agent for it,
// gives it to an agent they have, or leaves it empty. Nothing is forced.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { anythingChosen, apartProblem, choiceFrom, choiceOptions, choiceValue, chosenToast, emptyChoices, holderLine, newAgentForEach, planRolesKey, sameModelWarnings, toolLine } from "./lib/roles";
import type { AgentOption, Choice, Choices, RoleInfo, RolesView } from "./lib/roles";
import { ROLES_KEY, rolesApi } from "./rolesApi";
import { APPS_KEYS } from "./queries";
import { ErrorCard, LoadingCard, Notice, SMALL_BTN, SectionHeading, Tag } from "./ui";

function RoleCard({ role, roles, agents, choices, onChoose, disabled, children }: {
  role: RoleInfo;
  roles: readonly RoleInfo[];
  agents: readonly AgentOption[];
  choices: Choices;
  onChoose: (value: string) => void;
  disabled: boolean;
  children?: ReactNode;
}) {
  const selectId = useId();
  const apart = role.notWith.map((id) => roles.find((r) => r.id === id)?.label ?? id);
  return (
    <li className="rounded-lg border border-border bg-surface px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h4 className="text-[13px] font-semibold">{role.label}</h4>
        {role.optional ? null : <Tag tone="warn">Asked for</Tag>}
        <Tag>Suggested model: {role.model}</Tag>
      </div>
      {role.purpose ? <p className="mt-0.5 break-words text-[13px] text-text-muted">{role.purpose}</p> : null}
      {children}
      <ul className="mt-1.5 flex flex-col gap-1 text-xs text-text-muted" aria-label={`Tools of ${role.label}`}>
        {role.tools.map(toolLine).map((tool) => (
          <li key={tool.name} className="break-words">
            <span className="text-text">{tool.summary}</span> <span>({tool.effect})</span>{" "}
            <span className="font-mono text-[10px] text-text-faint">{tool.name}</span>
          </li>
        ))}
      </ul>
      {apart.length ? <p className="mt-1 text-xs text-text-muted">Never held by the same agent as {apart.join(" or ")}.</p> : null}
      <details className="mt-1.5 text-xs">
        <summary className="cursor-pointer text-text-muted">Read what the agent will be given (its charter)</summary>
        {role.charter === null ? (
          <p className="mt-1 text-danger">The charter could not be read.</p>
        ) : (
          <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-surface-2 p-2 font-mono text-[11px]">{role.charter}</pre>
        )}
        <p className="mt-1 text-text-faint">It is text from the app. It cannot change what the agent may do, and no level on the ladder changes.</p>
      </details>
      <label htmlFor={selectId} className="mt-2 block text-xs font-medium">
        Who does this
      </label>
      <select id={selectId} className="mt-1 w-full rounded-lg border border-border bg-surface px-2 py-1.5 text-[13px]" value={choiceValue(choices[role.id])} disabled={disabled} onChange={(event) => onChoose(event.target.value)}>
        {choiceOptions(role, roles, agents, choices).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </li>
  );
}

/** The choices for a set of roles, with the checks the server makes again shown as you go. */
function RoleChoices({ appName, view, choices, setChoices, disabled }: { appName: string; view: RolesView; choices: Choices; setChoices: (next: Choices) => void; disabled: boolean }) {
  const problem = apartProblem(appName, view.roles, choices);
  const warnings = sameModelWarnings(appName, view.roles, view.agents, choices);
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex flex-col gap-2">
        {view.roles.map((role) => (
          <RoleCard key={role.id} role={role} roles={view.roles} agents={view.agents} choices={choices} disabled={disabled} onChoose={(value) => setChoices({ ...choices, [role.id]: choiceFrom(value) })} />
        ))}
      </ul>
      {problem ? <Notice tone="bad">{problem}</Notice> : null}
      {warnings.map((text) => (
        <Notice key={text} tone="warn">
          {text}
        </Notice>
      ))}
    </div>
  );
}

/** The roles step of the install dialog: read before the owner agrees, sent once the app is installed. */
/** The roles a waiting install would like filled. */
export function usePlanRoles(plan: string | null) {
  // Read once: the plan is gone as soon as the app is installed, so it is never asked again.
  return useQuery({
    queryKey: planRolesKey(plan ?? ""),
    queryFn: () => rolesApi.forPlan(plan ?? ""),
    enabled: plan !== null,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function RoleStep({ plan, appName, choices, onChoices, disabled }: { plan: string; appName: string; choices: Choices; onChoices: (next: Choices) => void; disabled: boolean }) {
  const query = usePlanRoles(plan);
  const view = query.data;
  useEffect(() => {
    if (view && Object.keys(choices).length === 0 && view.roles.length) onChoices(emptyChoices(view.roles));
  }, [view, choices, onChoices]);
  if (query.isPending || !view || view.roles.length === 0) return null;
  return (
    <section aria-labelledby="apps-plan-roles">
      <h3 id="apps-plan-roles" className="mb-1 text-[13px] font-semibold">
        Roles it would like filled
      </h3>
      <p className="mb-2 text-[13px] text-text-muted">
        {appName} can work with agents from your Village. Each role is optional: left empty, the app does that part its plain way. You can change this later on the app&rsquo;s page.
      </p>
      <button type="button" className={`${SMALL_BTN} mb-2`} disabled={disabled} onClick={() => onChoices(newAgentForEach(view.roles))}>
        Create an agent for each role
      </button>
      <RoleChoices appName={appName} view={view} choices={choices} setChoices={onChoices} disabled={disabled} />
    </section>
  );
}

/** Whether the install button must wait: one agent chosen for two roles kept apart. */
export function roleStepBlocks(appName: string, view: RolesView | undefined, choices: Choices): boolean {
  return Boolean(view && apartProblem(appName, view.roles, choices));
}

/** Send the install step's choices, once the app is installed. Says how it went. */
export async function sendInstallChoices(appId: string, appName: string, choices: Choices): Promise<string | null> {
  if (!anythingChosen(choices)) return null;
  return chosenToast(appName, await rolesApi.choose(appId, choices));
}

function pick(choices: Choices, roles: readonly string[]): Choices {
  const out: Choices = {};
  for (const role of roles) {
    const choice = choices[role];
    if (choice) out[role] = choice;
  }
  return out;
}

/** The Roles panel on an app's page: who holds each role, change it, empty it. */
export function RolesPanel({ id, name }: { id: string; name: string }) {
  const client = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: ROLES_KEY(id), queryFn: () => rolesApi.forApp(id) });
  const [choices, setChoices] = useState<Choices>({});
  const save = useMutation({
    mutationFn: (chosen: Choices) => rolesApi.choose(id, chosen),
    onSuccess: (result) => {
      toast.success(chosenToast(name, result));
      setChoices({});
    },
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ROLES_KEY(id) });
      void client.invalidateQueries({ queryKey: APPS_KEYS.all });
    },
  });

  if (query.isPending) return <LoadingCard>Loading the roles…</LoadingCard>;
  if (query.isError && query.error instanceof ApiError && query.error.status === 404) return null;
  if (query.isError) return <ErrorCard what="The roles" error={query.error} onRetry={() => void query.refetch()} />;
  const view = query.data;
  if (view.roles.length === 0) return null;
  const current: Choices = Object.fromEntries(
    view.roles.map((role): [string, Choice] => [role.id, role.holder ? { mode: "existing", agentId: role.holder.agentId } : { mode: "empty" }]),
  );
  const shown: Choices = { ...current, ...choices };
  const changed = Object.keys(choices).filter((role) => choiceValue(choices[role]) !== choiceValue(current[role]));
  const problem = apartProblem(name, view.roles, shown);

  return (
    <>
      <SectionHeading id="apps-detail-roles">Roles</SectionHeading>
      <section aria-labelledby="apps-detail-roles" className="flex flex-col gap-2 rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
        <p className="text-text-muted">The jobs {name} would like your agents to do. Each one is optional. Taking a role away keeps the agent.</p>
        <ul className="flex flex-col gap-2">
          {view.roles.map((role) => (
            <RoleCard key={role.id} role={role} roles={view.roles} agents={view.agents} choices={shown} disabled={save.isPending} onChoose={(value) => setChoices({ ...choices, [role.id]: choiceFrom(value) })}>
              <p className="mt-1 text-xs">{holderLine(role)}</p>
              {role.charterChanged && role.holder ? (
                <Notice tone="warn">
                  This version of the app has a new charter for {role.label}. {role.holder.label} still has the one you saw before.{" "}
                  <button type="button" className="font-medium text-accent underline" disabled={save.isPending} onClick={() => role.holder && save.mutate({ [role.id]: { mode: "existing", agentId: role.holder.agentId } })}>
                    Give the new charter
                  </button>
                </Notice>
              ) : null}
              {role.holder ? (
                <button type="button" className={`${SMALL_BTN} mt-1.5`} disabled={save.isPending} onClick={() => save.mutate({ [role.id]: { mode: "empty" } })}>
                  Empty this role
                </button>
              ) : null}
            </RoleCard>
          ))}
        </ul>
        {problem ? <Notice tone="bad">{problem}</Notice> : null}
        {[...view.warnings, ...sameModelWarnings(name, view.roles, view.agents, shown)].filter((t, i, all) => all.indexOf(t) === i).map((text) => (
          <Notice key={text} tone="warn">
            {text}
          </Notice>
        ))}
        {changed.length ? (
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" className={SMALL_BTN} disabled={save.isPending} onClick={() => setChoices({})}>
              Undo changes
            </button>
            <button type="button" className={`${SMALL_BTN} btn-primary`} disabled={save.isPending || problem !== null} onClick={() => save.mutate(pick(choices, changed))}>
              Save roles
            </button>
          </div>
        ) : null}
      </section>
    </>
  );
}
