import { TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";
import { errorDetail } from "../../api";
import { modelsApi } from "./api";
import type { Provider, RoleEntry, RolesResponse, Target } from "./api";
import { roleUse, roleWarnings, viewTarget } from "./lib/roles";
import { RoleDialog } from "./RoleDialog";
import { TryDialog } from "./TryDialog";
import type { TryState } from "./TryDialog";
import { ErrorCard, LoadingCard, LocalityTag, SMALL_BTN, SectionHeading, Tag } from "./ui";

/** What "Try it" sends first. The owner can change it in the dialog and run again. */
const TEST_MESSAGE = "Reply with one short sentence to show you are working.";

type RolesSectionProps = {
  roles: { data: RolesResponse | undefined; isPending: boolean; isError: boolean; error: unknown; refetch: () => unknown };
  providers: readonly Provider[] | undefined;
};

function TargetLine({ target, providers }: { target: Target; providers: readonly Provider[] }) {
  const view = viewTarget(target, providers);
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-0.5">
      <span className="min-w-0 break-all font-mono text-xs text-text">{view.model}</span>
      <span className={`min-w-0 break-words text-xs ${view.state === "ok" ? "text-text-muted" : "text-warning"}`}>
        {view.providerLabel}
        {view.state === "off" ? " (switched off)" : view.state === "missing" ? " (removed)" : ""}
      </span>
      <LocalityTag isLocal={view.isLocal} />
      {view.effort ? <Tag title="Effort">{view.effort}</Tag> : null}
    </span>
  );
}

/** The roles table: what each role maps to, with edit and "Try it". */
export function RolesSection({ roles, providers }: RolesSectionProps) {
  const [editing, setEditing] = useState<string | null>(null);
  const [trying, setTrying] = useState<{ role: string; prompt: string; state: TryState } | null>(null);
  // Only the latest run may write its answer into the dialog.
  const run = useRef(0);

  const tryRole = async (role: string, prompt: string) => {
    const turn = ++run.current;
    setTrying({ role, prompt, state: { pending: true } });
    let state: TryState;
    try {
      state = { pending: false, result: await modelsApi.tryModel({ prompt, role }) };
    } catch (error) {
      state = { pending: false, failure: errorDetail(error) };
    }
    if (run.current === turn) setTrying((current) => (current ? { ...current, state } : current));
  };

  const closeTry = () => {
    run.current += 1;
    setTrying(null);
  };

  const list = roles.data?.roles ?? [];
  const known = providers ?? [];
  const edited = editing ? list.find((entry) => entry.role === editing) : undefined;
  const grid = "@3xl:grid @3xl:grid-cols-[6.5rem_minmax(0,0.9fr)_minmax(0,1.3fr)_minmax(0,1.3fr)_auto] @3xl:items-start @3xl:gap-x-3";

  const row = (entry: RoleEntry) => {
    const warnings = providers ? roleWarnings(entry, known) : [];
    const use = roleUse(entry.role);
    const unset = entry.primary === null;
    return (
      <li key={entry.role} className={`flex flex-col gap-1.5 border-b border-border/55 px-3 py-2.5 last:border-b-0 ${grid}`}>
        <span className="font-mono text-xs font-semibold @3xl:pt-0.5">{entry.role}</span>
        <span className="text-xs text-text-muted @3xl:pt-0.5">{use}</span>
        <div className="min-w-0">
          <span className="font-mono text-[10.5px] uppercase tracking-widest text-text-faint @3xl:sr-only">Model </span>
          {entry.primary ? (
            <TargetLine target={entry.primary} providers={known} />
          ) : (
            <span className="text-xs text-text-faint">{entry.inherits_default ? "Uses the default role's models" : "Not set"}</span>
          )}
        </div>
        <div className="min-w-0">
          <span className="font-mono text-[10.5px] uppercase tracking-widest text-text-faint @3xl:sr-only">Fallback </span>
          {entry.fallbacks.length === 0 ? (
            <span className="text-xs text-text-faint">{unset ? "" : "None"}</span>
          ) : (
            <ol className="flex flex-col gap-1">
              {entry.fallbacks.map((target, index) => (
                <li key={index} className="flex min-w-0 items-start gap-1.5">
                  <span className="flex-none font-mono text-xs text-text-faint" aria-hidden="true">
                    {index + 1}.
                  </span>
                  <TargetLine target={target} providers={known} />
                </li>
              ))}
            </ol>
          )}
        </div>
        <div className="flex flex-none items-center gap-2 @3xl:justify-end">
          <button type="button" className={SMALL_BTN} onClick={() => setEditing(entry.role)} aria-label={`Edit the ${entry.role} role`} disabled={!providers}>
            Edit
          </button>
          <button
            type="button"
            className={SMALL_BTN}
            onClick={() => void tryRole(entry.role, TEST_MESSAGE)}
            aria-label={`Try the ${entry.role} role`}
            disabled={unset && !entry.inherits_default}
            title={unset && !entry.inherits_default ? "Choose a model for this role first." : "Send a short test message to this role"}
          >
            Try it
          </button>
        </div>
        {warnings.length > 0 ? (
          <ul className="flex flex-col gap-1 @3xl:col-span-5" aria-label={`Warnings for the ${entry.role} role`}>
            {warnings.map((warning) => (
              <li key={warning} className="flex items-start gap-1.5 text-xs text-warning">
                <TriangleAlert size={13} className="mt-0.5 flex-none" aria-hidden="true" />
                <span className="min-w-0 break-words">{warning}</span>
              </li>
            ))}
          </ul>
        ) : null}
      </li>
    );
  };

  return (
    <section aria-labelledby="models-roles-heading">
      <SectionHeading id="models-roles-heading">Roles</SectionHeading>
      {roles.isPending ? (
        <LoadingCard>Loading roles…</LoadingCard>
      ) : roles.isError ? (
        <ErrorCard what="Roles" error={roles.error} onRetry={() => void roles.refetch()} />
      ) : (
        <div className="rounded-xl border border-border bg-surface">
          <div className={`hidden border-b border-border/55 px-3 py-2 font-mono text-[10.5px] uppercase tracking-widest text-text-faint ${grid}`} aria-hidden="true">
            <span>Role</span>
            <span>Used for</span>
            <span>Model</span>
            <span>Fallback</span>
            <span />
          </div>
          <ul>{list.map(row)}</ul>
          {providers && providers.length === 0 ? <p className="border-t border-border/55 px-3 py-2 text-xs text-text-faint">Add a provider above before giving a role a model.</p> : null}
        </div>
      )}
      {edited && providers ? <RoleDialog entry={edited} providers={providers} onClose={() => setEditing(null)} /> : null}
      {trying ? (
        <TryDialog role={trying.role} prompt={trying.prompt} state={trying.state} providers={known} onRun={(prompt) => void tryRole(trying.role, prompt)} onClose={closeTry} />
      ) : null}
    </section>
  );
}
