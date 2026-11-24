import { Loader2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { appsApi } from "./api";
import type { AppDetail, UpdatePlan, UpdateSource } from "./api";
import { canUpdate, updateWords } from "./lib/update";
import { useConfirmUpdate } from "./queries";
import { Notice, PermissionSentence } from "./ui";

type Props = { app: Pick<AppDetail, "id" | "name" | "version" | "update" | "source">; onClose: () => void };

/**
 * Update an app in two steps, as installing is. Step one stages the new version and
 * answers with what is new; nothing changes. Step two puts exactly that in place. With an
 * update found, step one starts at once; otherwise the owner says where it comes from.
 */
export function UpdateDialog({ app, onClose }: Props) {
  const kindName = useId();
  const [plan, setPlan] = useState<UpdatePlan | null>(null);
  const [planning, setPlanning] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [kind, setKind] = useState<"folder" | "git">(app.source?.kind === "git" ? "git" : "folder");
  const [path, setPath] = useState("");
  const [url, setUrl] = useState(app.source?.repo ?? "");
  const [at, setAt] = useState("main");
  const confirm = useConfirmUpdate(onClose);
  const busy = planning || confirm.isPending;

  const ask = async (source: UpdateSource) => {
    setPlanning(true);
    setRefusal(null);
    try {
      setPlan(await appsApi.planUpdate(app.id, source));
    } catch (error) {
      setRefusal(errorDetail(error));
    } finally {
      setPlanning(false);
    }
  };

  const found = app.update !== null && app.update !== undefined;
  useEffect(() => {
    if (found) void ask({ kind: "found" });
    // Asked once, when the dialog opens for an update that was found.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const close = () => {
    if (plan) void appsApi.cancelUpdate(plan);
    onClose();
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void ask(kind === "folder" ? { kind: "folder", path } : { kind: "git", url, commitOrRef: at });
  };
  const words = plan ? updateWords(plan) : null;

  return (
    <Modal title={words ? words.title : `Update ${app.name}`} onClose={busy ? () => undefined : close} widthClass="max-w-lg">
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto px-4 py-4 text-sm">
        {refusal ? <Notice tone="bad">{refusal}</Notice> : null}
        {plan && words ? (
          <>
            <p className="text-text-muted">{words.intro}</p>
            {words.tier ? <Notice tone="warn">{words.tier}</Notice> : null}
            {plan.storage ? <Notice tone="warn">{plan.storage}</Notice> : null}
            {words.ownNetwork ? <Notice tone={plan.localAddresses?.kept ? "calm" : "warn"}>{words.ownNetwork}</Notice> : null}
            {plan.warning ? (
              <p className="rounded-lg border border-danger bg-danger/10 px-3 py-2 text-[13px] font-semibold text-text" role="alert">
                {plan.warning}
              </p>
            ) : null}
            <section aria-labelledby="apps-update-new">
              <h3 id="apps-update-new" className="mb-2 text-[13px] font-semibold">
                {words.newHeading}
              </h3>
              {plan.ask.length > 0 ? (
                <ul className="flex flex-col gap-1.5" aria-label="What is new">
                  {plan.ask.map((line) => (
                    <PermissionSentence key={line.id} line={line} />
                  ))}
                </ul>
              ) : null}
            </section>
            {words.removed ? <p className="text-xs text-text-muted">{words.removed}</p> : null}
            {plan.needsTypedName ? (
              <Field
                label={`To update it, type its name: ${plan.app.name}`}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                disabled={busy}
              />
            ) : null}
          </>
        ) : found ? (
          <p className="text-text-muted" role="status">
            Reading the new version of {app.name}. Nothing changes until you agree.
          </p>
        ) : (
          <form id="apps-update-form" className="flex flex-col gap-3" onSubmit={submit}>
            <p className="text-text-muted">JimmyHQ has not found a newer version of {app.name}. Say where one is. Nothing changes until you have seen what it asks for.</p>
            <fieldset className="flex flex-wrap gap-3 text-[13px]" disabled={busy}>
              <legend className="sr-only">Where the new version is</legend>
              <label className="flex items-center gap-1.5">
                <input type="radio" name={kindName} checked={kind === "folder"} onChange={() => setKind("folder")} />A folder on this computer
              </label>
              <label className="flex items-center gap-1.5">
                <input type="radio" name={kindName} checked={kind === "git"} onChange={() => setKind("git")} />A git address
              </label>
            </fieldset>
            {kind === "folder" ? (
              <Field label="Folder" value={path} onChange={(event) => setPath(event.target.value)} placeholder="The full path of the app's folder" disabled={busy} required />
            ) : (
              <>
                <Field label="Address" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://git.example.com/apps/water.git" disabled={busy} required />
                <Field label="Commit, branch or tag" value={at} onChange={(event) => setAt(event.target.value)} hint="A branch or tag is looked up once and the commit it names is what is installed." disabled={busy} required />
              </>
            )}
          </form>
        )}
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={close} disabled={busy}>
          Cancel
        </button>
        {plan && words ? (
          <button type="button" className="btn btn-primary" disabled={busy || !canUpdate(plan, typed)} onClick={() => confirm.mutate({ plan, typed })}>
            {confirm.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
            {words.button}
          </button>
        ) : found ? null : (
          <button type="submit" form="apps-update-form" className="btn btn-primary" disabled={busy}>
            {planning ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
            See what is new
          </button>
        )}
      </div>
    </Modal>
  );
}
