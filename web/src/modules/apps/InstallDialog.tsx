import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { InstallRefused, appsApi } from "./api";
import type { AppSummary, InstallPlan, InstallSource } from "./api";
import { EMPTY_FORM, formProblems, formReady, formSource } from "./lib/install";
import type { InstallForm, SourceKind } from "./lib/install";
import { canInstall, planView } from "./lib/plan";
import type { PlanView } from "./lib/plan";
import { APPS_SETTINGS_HASH } from "./lib/route";
import { APPS_KEYS } from "./queries";
import { Notice, PermissionGroups, Tag, TierBadge } from "./ui";
import { RoleStep, roleStepBlocks, sendInstallChoices, usePlanRoles } from "./RolesPanel";
import type { Choices } from "./lib/roles";

type Props = {
  /** A source to read straight away, for "try one of the examples". */
  initialSource?: InstallSource;
  onClose: () => void;
  onInstalled?: (app: AppSummary) => void;
};

type Refusal = { message: string; problems: { field: string; message: string }[]; kind: "manifest" | "setting" | "other" };

function refusalOf(error: unknown): Refusal {
  if (error instanceof InstallRefused) return { message: error.detail, problems: error.problems, kind: error.kind };
  return { message: errorDetail(error), problems: [], kind: "other" };
}

/**
 * Install an app in two steps. Step one asks the server for a plan: what the app is, what it
 * may touch and what it will create. Nothing is installed. Step two installs exactly that
 * plan, by the fingerprint the server returned.
 */
export function InstallDialog({ initialSource, onClose, onInstalled }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<InstallForm>(EMPTY_FORM);
  const [showErrors, setShowErrors] = useState(false);
  const [plan, setPlan] = useState<InstallPlan | null>(null);
  const [busy, setBusy] = useState<"plan" | "install" | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [typed, setTyped] = useState("");
  const [roleChoices, setRoleChoices] = useState<Choices>({});
  const planRoles = usePlanRoles(plan ? plan.plan : null);
  const body = useRef<HTMLDivElement>(null);
  const kindName = useId();

  // Each step starts at the top of its own text, with focus on it so the keyboard is not left on a button that is gone.
  const step = plan ? "plan" : "source";
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    body.current?.scrollTo({ top: 0 });
    body.current?.focus();
  }, [step]);

  const problems = formProblems(form);
  const update = (change: Partial<InstallForm>) => {
    setForm((current) => ({ ...current, ...change }));
    setRefusal(null);
  };

  const readSource = async (source: InstallSource) => {
    setBusy("plan");
    setRefusal(null);
    try {
      setPlan(await appsApi.plan(source));
      setTyped("");
      setRoleChoices({});
    } catch (error) {
      setRefusal(refusalOf(error));
    } finally {
      setBusy(null);
    }
  };

  const showPlan = (event: FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    const source = formSource(form);
    if (source && !busy) void readSource(source);
  };

  // An example is read as soon as the window opens: there is nothing to type.
  const asked = useRef(false);
  useEffect(() => {
    if (!initialSource || asked.current) return;
    asked.current = true;
    void readSource(initialSource);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialSource]);

  /** The owner said no: the fetched app is thrown away on the server too. */
  const drop = () => {
    if (plan) void appsApi.cancel(plan);
  };

  const install = async () => {
    if (!plan || busy) return;
    setBusy("install");
    setRefusal(null);
    try {
      const app = await appsApi.install(plan, typed);
      void client.invalidateQueries({ queryKey: APPS_KEYS.all });
      toast.success(`Installed ${app.name}. It is switched on.`);
      try {
        const said = await sendInstallChoices(app.id, app.name, roleChoices);
        if (said) toast.success(said);
      } catch (error) {
        toast.error(`${app.name} is installed, and its roles were not filled: ${errorDetail(error)} You can fill them on the app's page.`);
      }
      onInstalled?.(app);
      onClose();
    } catch (error) {
      setRefusal(refusalOf(error));
      setBusy(null);
    }
  };

  const view = plan ? planView(plan) : null;
  const title = view ? view.title : "Install an app";

  return (
    <Modal title={title} onClose={busy ? () => undefined : onClose} panelClass="max-h-[88vh] w-full max-w-xl">
      <div ref={body} tabIndex={-1} className="@container min-h-0 flex-1 overflow-y-auto px-4 py-4 outline-none">
        {view && plan ? (
          <>
            <PlanStep view={view} typed={typed} onTyped={setTyped} disabled={busy !== null} />
            {view.tier.key === "full" ? (
              <div className="mt-4">
                <RoleStep plan={plan.plan} appName={view.name} choices={roleChoices} onChoices={setRoleChoices} disabled={busy !== null} />
              </div>
            ) : null}
          </>
        ) : (
          <form id="apps-install-form" onSubmit={showPlan} className="flex flex-col gap-4" noValidate>
            <p className="text-sm text-text-muted">
              Choose where the app is. JimmyHQ reads it and shows you what it is and what it may touch. Nothing is installed, and none of its code runs, until you say so.
            </p>
            <fieldset className="flex flex-col gap-2" disabled={busy !== null}>
              <legend className="mb-1 text-xs font-medium text-text">Where is the app?</legend>
              <div className="grid gap-2 @md:grid-cols-2">
                {(["folder", "git"] as SourceKind[]).map((kind) => (
                  <label key={kind} className="flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm has-[:checked]:border-accent has-[:checked]:bg-surface-2">
                    <input type="radio" name={kindName} checked={form.kind === kind} onChange={() => update({ kind })} />
                    {kind === "folder" ? "A folder on this computer" : "A git address"}
                  </label>
                ))}
              </div>
              <p className="text-xs text-text-faint">The store opens in a later version. Until then, install from a folder or a git address.</p>
            </fieldset>

            {form.kind === "folder" ? (
              <Field
                label="Folder"
                value={form.path}
                onChange={(event) => update({ path: event.target.value })}
                placeholder="The full path of the app's folder"
                hint="The folder that holds jimmyhq.app.json. It is copied into JimmyHQ, so you can move or delete the original afterwards."
                error={showErrors ? (problems.path ?? undefined) : undefined}
                autoComplete="off"
                spellCheck={false}
                disabled={busy !== null}
                data-autofocus
              />
            ) : (
              <>
                <Field
                  label="Git address"
                  value={form.url}
                  onChange={(event) => update({ url: event.target.value })}
                  placeholder="https://git.example.com/apps/water.git"
                  hint="An https:// address. Do not put a name or a password in it."
                  error={showErrors ? (problems.url ?? undefined) : undefined}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy !== null}
                  data-autofocus
                />
                <Field
                  label="Commit"
                  value={form.commit}
                  onChange={(event) => update({ commit: event.target.value })}
                  placeholder="The full 40-character hash"
                  hint="The app is pinned to this exact commit, so it cannot change after you have read what it asks for."
                  error={showErrors ? (problems.commit ?? undefined) : undefined}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy !== null}
                />
              </>
            )}
            {refusal ? <RefusalNotice refusal={refusal} onNavigate={onClose} /> : null}
          </form>
        )}
        {view && refusal ? (
          <div className="mt-4">
            <RefusalNotice refusal={refusal} onNavigate={onClose} />
          </div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
        {view ? (
          <>
            <button type="button" className="btn" disabled={busy !== null} onClick={() => { drop(); setPlan(null); setRefusal(null); }}>
              Back
            </button>
            <button type="button" className="btn" disabled={busy !== null} onClick={() => { drop(); onClose(); }}>
              Cancel
            </button>
            <button
              type="button"
              className={`btn ${view.installTone === "danger" ? "btn-danger" : "btn-primary"}`}
              disabled={busy !== null || !canInstall(view, typed) || roleStepBlocks(view.name, planRoles.data, roleChoices)}
              onClick={() => void install()}
            >
              {busy === "install" ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
              {view.installLabel}
            </button>
          </>
        ) : (
          <>
            <button type="button" className="btn" disabled={busy !== null} onClick={onClose}>
              Cancel
            </button>
            <button type="submit" form="apps-install-form" className="btn btn-primary" disabled={busy !== null || (showErrors && !formReady(form))}>
              {busy === "plan" ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
              {busy === "plan" ? "Reading the app" : "See what it asks for"}
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}

function PlanStep({ view, typed, onTyped, disabled }: { view: PlanView; typed: string; onTyped: (value: string) => void; disabled: boolean }) {
  const weight = view.warning.weight;
  const warningTone =
    weight === "blunt" ? "border-danger bg-danger/10" : weight === "red" ? "border-danger/50 bg-danger/10" : weight === "warn" ? "border-warning/50 bg-warning/10" : "border-border bg-surface-2";
  const headingTone = weight === "blunt" || weight === "red" ? "text-danger" : "text-text";
  return (
    <div className="flex flex-col gap-4">
      <section aria-labelledby="apps-plan-what">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h3 id="apps-plan-what" className="min-w-0 break-words text-base font-semibold">
            {view.name}
          </h3>
          <span className="font-mono text-xs text-text-muted">{view.version}</span>
          <TierBadge tier={view.tier.key} />
          {view.licence ? <Tag>{view.licence}</Tag> : null}
        </div>
        {view.summary ? <p className="mt-1 break-words text-sm text-text-muted">{view.summary}</p> : null}
        <p className="mt-1 text-xs text-text-muted">{view.tier.sentence}</p>
        <dl className="mt-2 grid gap-x-3 gap-y-1 text-xs @md:grid-cols-[auto_1fr]">
          <dt className="text-text-faint">Made by</dt>
          <dd className="break-words">{view.madeBy}</dd>
          <dt className="text-text-faint">From</dt>
          <dd className={view.commit ? "break-all" : "break-words"}>{view.sourceLine}</dd>
          {view.commit ? (
            <>
              <dt className="text-text-faint">Commit</dt>
              <dd className="break-all font-mono">{view.commit}</dd>
            </>
          ) : null}
        </dl>
      </section>

      <div className={`rounded-lg border px-3 py-2.5 ${warningTone}`} role={view.warning.weight === "blunt" ? "alert" : undefined}>
        <p className={`text-sm font-semibold ${headingTone}`}>{view.warning.heading}</p>
        <p className={`mt-0.5 text-[13px] text-text ${weight === "blunt" ? "font-medium" : ""}`}>{view.warning.text}</p>
      </div>

      {view.notRunning ? <Notice tone="calm">{view.notRunning}</Notice> : null}

      {view.replaces ? <Notice tone="warn">{view.replaces}</Notice> : null}
      {view.blocked ? <Notice tone="bad">{view.blocked}</Notice> : null}

      <section aria-labelledby="apps-plan-may">
        <h3 id="apps-plan-may" className="mb-2 text-[13px] font-semibold">
          What it may do
        </h3>
        <PermissionGroups groups={view.groups} />
        {view.storage ? (
          <div className="mt-2">
            <Notice tone="warn">{view.storage}</Notice>
          </div>
        ) : null}
        {view.feeds.map((line) => (
          <div key={line} className="mt-2">
            <Notice tone="calm">{line}</Notice>
          </div>
        ))}
      </section>

      <section aria-labelledby="apps-plan-creates">
        <h3 id="apps-plan-creates" className="mb-2 text-[13px] font-semibold">
          What installing creates
        </h3>
        <ul className="flex flex-col gap-1 text-[13px] text-text-muted">
          {view.creates.map((text) => (
            <li key={text} className="flex gap-2">
              <span aria-hidden="true" className="mt-[7px] size-1 flex-none rounded-full bg-text-faint" />
              <span className="min-w-0 break-words">{text}</span>
            </li>
          ))}
        </ul>
      </section>

      {view.nameToType ? (
        <Field
          label={`To install it, type its name: ${view.nameToType}`}
          value={typed}
          onChange={(event) => onTyped(event.target.value)}
          hint={view.typedHint ?? undefined}
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
        />
      ) : null}
    </div>
  );
}

function RefusalNotice({ refusal, onNavigate }: { refusal: Refusal; onNavigate: () => void }) {
  if (refusal.kind === "manifest") {
    return (
      <Notice tone="bad" title={`${refusal.message} Nothing was installed.`}>
        <p className="mb-1.5">The author can fix these in the app&rsquo;s jimmyhq.app.json, and then you try again.</p>
        <ul className="flex flex-col gap-1" aria-label="Problems with the manifest">
          {refusal.problems.map((problem, index) => (
            <li key={`${problem.field}-${index}`} className="flex flex-wrap gap-x-2">
              {problem.field ? <code className="rounded border border-border bg-surface px-1 font-mono text-[11px]">{problem.field}</code> : null}
              <span className="min-w-0 break-words">{problem.message}</span>
            </li>
          ))}
        </ul>
      </Notice>
    );
  }
  if (refusal.kind === "setting") {
    return (
      <Notice tone="warn" title="Your settings do not allow this">
        <p className="break-words">{refusal.message}</p>
        <p className="mt-1">
          You can change this under{" "}
          <a href={APPS_SETTINGS_HASH} className="font-medium text-accent underline" onClick={onNavigate}>
            Settings, Apps
          </a>
          . Nothing was installed.
        </p>
      </Notice>
    );
  }
  return <Notice tone="bad">{refusal.message}</Notice>;
}
