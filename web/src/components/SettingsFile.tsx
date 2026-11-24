import { useQueryClient } from "@tanstack/react-query";
import { Download, Upload } from "lucide-react";
import { useId, useState } from "react";
import { api, errorDetail, settingsExportUrl } from "../api";
import type { Setting, SettingsPlan } from "../api";
import { SETTINGS_QUERY_KEY } from "../hooks/useSettings";
import { useToast } from "../hooks/useToast";
import { SETTINGS_CLI_HELP, changeLine, changedMessage, parseSettingsText } from "../lib/settingsFile";
import { Modal } from "./Modal";

/**
 * The Import and export page: save the settings as a JSON file, read one back,
 * and the same commands for a terminal.
 */
export function SettingsFile({ settings }: { settings: readonly Setting[] }) {
  const [dialog, setDialog] = useState<"export" | "import" | null>(null);
  return (
    <div className="flex flex-col gap-3">
      <ul className="divide-y divide-border rounded-xl border border-border bg-surface">
        <li className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3.5">
          <div className="min-w-0 flex-1 basis-64">
            <h3 className="text-[13px] font-medium text-text">Export</h3>
            <p className="mt-0.5 text-xs text-text-muted">
              Save your settings as a file: a profile that is safe to share, or a personal backup of everything. Keys and passwords are never included.
            </p>
          </div>
          <button type="button" className="btn flex-none" onClick={() => setDialog("export")}>
            <Download size={14} aria-hidden="true" />
            Export…
          </button>
        </li>
        <li className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3.5">
          <div className="min-w-0 flex-1 basis-64">
            <h3 className="text-[13px] font-medium text-text">Import</h3>
            <p className="mt-0.5 text-xs text-text-muted">Read a settings file. You see every change it would make, and nothing changes until you say so.</p>
          </div>
          <button type="button" className="btn flex-none" onClick={() => setDialog("import")}>
            <Upload size={14} aria-hidden="true" />
            Import…
          </button>
        </li>
      </ul>
      <section aria-labelledby="settings-file-terminal" className="rounded-xl border border-border bg-surface px-4 py-3.5">
        <h3 id="settings-file-terminal" className="text-[13px] font-medium text-text">
          In a terminal
        </h3>
        <p className="mt-0.5 text-xs text-text-muted">The same, as commands on the computer JimmyHQ runs on.</p>
        <ul className="mt-2 flex flex-col gap-1.5">
          {SETTINGS_CLI_HELP.map((command) => (
            <li key={command}>
              <code className="inline-block max-w-full break-words rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[11px]">{command}</code>
            </li>
          ))}
        </ul>
      </section>
      {dialog === "export" ? <ExportDialog onClose={() => setDialog(null)} /> : null}
      {dialog === "import" ? <ImportDialog settings={settings} onClose={() => setDialog(null)} /> : null}
    </div>
  );
}

function ExportDialog({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<"profile" | "everything">("profile");
  const [changed, setChanged] = useState(false);
  const name = useId();
  return (
    <Modal title="Export settings" onClose={onClose}>
      <div className="flex flex-col gap-3 overflow-y-auto px-4 py-4 text-sm">
        <fieldset className="flex flex-col gap-2">
          <legend className="sr-only">What to export</legend>
          <label className="flex cursor-pointer items-start gap-2.5">
            <input
              type="radio"
              name={name}
              className="mt-1 accent-accent"
              checked={kind === "profile"}
              onChange={() => setKind("profile")}
              data-autofocus
            />
            <span>
              <span className="font-medium">As a shareable profile</span>
              <span className="block text-xs text-text-muted">
                Leaves out what is personal or tied to this computer: your name, time zone, server names and addresses, and folders.
              </span>
            </span>
          </label>
          <label className="flex cursor-pointer items-start gap-2.5">
            <input type="radio" name={name} className="mt-1 accent-accent" checked={kind === "everything"} onChange={() => setKind("everything")} />
            <span>
              <span className="font-medium">Everything</span>
              <span className="block text-xs text-text-muted">A personal backup of every setting. Keep it to yourself.</span>
            </span>
          </label>
        </fieldset>
        <label className="flex cursor-pointer items-center gap-2.5">
          <input type="checkbox" className="accent-accent" checked={changed} onChange={(event) => setChanged(event.target.checked)} />
          <span>Only the settings I have changed</span>
        </label>
        <p className="text-xs text-text-faint">Passwords, keys and tokens are never in a settings file.</p>
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
        <a className="btn btn-primary" href={settingsExportUrl({ portable: kind === "profile", changed })} download onClick={onClose}>
          Download
        </a>
      </div>
    </Modal>
  );
}

type Preview = { text: string; plan: SettingsPlan };

function ImportDialog({ settings, onClose }: { settings: readonly Setting[]; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const fileId = useId();
  const pasteId = useId();

  const refresh = () => client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });

  const pick = async (file: File | undefined) => {
    if (!file) return;
    setError("");
    try {
      setText(await file.text());
      setFileName(file.name);
    } catch {
      setError("That file could not be read. Try another, or paste its contents.");
    }
  };

  const check = async () => {
    const parsed = parseSettingsText(text);
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    setBusy(true);
    setError("");
    try {
      setPreview({ text, plan: await api.planSettings(parsed.document) });
    } catch (failure) {
      setError(errorDetail(failure));
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    const parsed = preview ? parseSettingsText(preview.text) : null;
    if (!preview || !parsed?.ok) return;
    setBusy(true);
    setError("");
    try {
      // The owner has seen what is skipped, so what cannot be applied is left out.
      const result = await api.importSettings(parsed.document, true);
      await refresh();
      const count = result.changes.length;
      onClose();
      toast.success(
        changedMessage(count),
        count === 0
          ? undefined
          : {
              label: "Undo",
              run: () => {
                api
                  .importSettings(result.undo, false)
                  .then((undone) => {
                    void refresh();
                    toast.success(`Put ${undone.changes.length} ${undone.changes.length === 1 ? "setting" : "settings"} back.`);
                  })
                  .catch((failure: unknown) => toast.error(errorDetail(failure)));
              },
            },
      );
    } catch (failure) {
      setError(errorDetail(failure));
      setBusy(false);
    }
  };

  if (preview) {
    const { plan } = preview;
    return (
      <Modal title="Import settings" onClose={onClose} widthClass="max-w-xl">
        <div className="flex flex-col gap-3 overflow-y-auto px-4 py-4 text-sm">
          {plan.name || plan.description ? (
            <div>
              {plan.name ? <p className="font-medium">{plan.name}</p> : null}
              {plan.description ? <p className="text-xs text-text-muted">{plan.description}</p> : null}
            </div>
          ) : null}
          {plan.changes.length > 0 ? (
            <div>
              <h3 className="mb-1.5 text-xs font-semibold text-text-muted">
                {plan.changes.length} {plan.changes.length === 1 ? "change" : "changes"}
              </h3>
              <ul className="divide-y divide-border rounded-lg border border-border">
                {plan.changes.map((entry) => {
                  const line = changeLine(entry, settings);
                  return (
                    <li key={entry.key} className="px-3 py-2">
                      <span className="font-medium">{line.label}</span>
                      <span className="text-text-muted">
                        : {line.from} <span aria-label="to">→</span> {line.to}
                      </span>
                      {entry.portable === false ? <span className="ml-2 text-xs text-text-faint">personal to one computer</span> : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : (
            <p className="text-text-muted">Nothing in this file would change your settings.</p>
          )}
          {plan.refused.length > 0 ? (
            <div>
              <h3 className="mb-1.5 text-xs font-semibold text-danger">Skipped ({plan.refused.length})</h3>
              <ul className="divide-y divide-border rounded-lg border border-border">
                {plan.refused.map((entry) => (
                  <li key={entry.key} className="px-3 py-2">
                    <span className="font-medium">{entry.label ?? entry.key}</span>
                    <span className="block text-xs text-text-muted">{entry.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {plan.unchanged.length > 0 ? (
            <p className="text-xs text-text-faint">
              {plan.unchanged.length} {plan.unchanged.length === 1 ? "setting is" : "settings are"} already as the file says.
            </p>
          ) : null}
          {error ? (
            <p className="text-xs text-danger" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex justify-between gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={() => setPreview(null)} disabled={busy}>
            Back
          </button>
          <div className="flex gap-2">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void apply()} disabled={busy || plan.changes.length === 0} data-autofocus>
              Apply
            </button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Import settings" onClose={onClose} widthClass="max-w-xl">
      <div className="flex flex-col gap-3 overflow-y-auto px-4 py-4 text-sm">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={fileId} className="text-xs font-medium">
            Settings file
          </label>
          <input
            id={fileId}
            type="file"
            accept=".json,application/json"
            className="text-xs"
            onChange={(event) => void pick(event.target.files?.[0])}
            data-autofocus
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={pasteId} className="text-xs font-medium">
            {fileName ? `Contents of ${fileName}` : "Or paste its contents"}
          </label>
          <textarea
            id={pasteId}
            className="input h-40 py-2 font-mono text-xs"
            spellCheck={false}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setFileName("");
            }}
          />
        </div>
        {error ? (
          <p className="text-xs text-danger" role="alert">
            {error}
          </p>
        ) : null}
        <p className="text-xs text-text-faint">You will see every change before anything is applied.</p>
      </div>
      <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" className="btn" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" onClick={() => void check()} disabled={busy}>
          Preview changes
        </button>
      </div>
    </Modal>
  );
}
