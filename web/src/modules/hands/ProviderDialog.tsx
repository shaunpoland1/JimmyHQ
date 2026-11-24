import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Field } from "../../components/Field";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { handsApi } from "./api";
import type { Provider, ProviderType } from "./api";
import { keyProblem, labelProblem, providerIdProblem, providerSlug, secretNameProblem } from "./lib/connect";
import { SHAPE_LABELS, addressField, isList, missingSecrets, optionFields, optionsFromValues, sameOptions, shapesOf, startShape, valuesFromOptions } from "./lib/provider";
import type { FieldValues, Shape } from "./lib/provider";
import { healthTone, removeKeyQuestion, removeSecretQuestion, secretsSentence, typeAbout } from "./lib/wording";
import { HANDS_KEYS, useAccountsQuery, useProvidersQuery } from "./queries";
import { AreaField, Notice, Pill, SMALL_BTN, SelectField } from "./ui";

type Props = { mode: "add"; types: ProviderType[]; onClose: () => void } | { mode: "edit"; provider: Provider; types: ProviderType[]; onClose: () => void };

/** Add a provider, or change one: its name, address, key and any other secrets. A saved key is never shown again. */
export function ProviderDialog(props: Props) {
  const { types, onClose } = props;
  const client = useQueryClient();
  const toast = useToast();
  const providers = useProvidersQuery().data?.providers ?? [];
  const start = props.mode === "edit" ? props.provider : null;
  // After a provider is created but a later step fails, the dialog carries on as an edit of it.
  const [created, setCreated] = useState<Provider | null>(null);
  const saved = start ? (providers.find((p) => p.id === start.id) ?? start) : (created ? (providers.find((p) => p.id === created.id) ?? created) : null);
  const editing = saved !== null;

  const [type, setType] = useState(saved?.type ?? types[0]?.type ?? "");
  const [label, setLabel] = useState(saved?.label ?? "");
  const [address, setAddress] = useState(saved?.base_url ?? "");
  const [id, setId] = useState("");
  const [key, setKey] = useState("");
  const [replacingKey, setReplacingKey] = useState(false);
  // Removing a saved key or secret asks first: it is never shown, so it cannot be brought back.
  const [confirmingKeyRemoval, setConfirmingKeyRemoval] = useState(false);
  const [removingSecret, setRemovingSecret] = useState<string | null>(null);
  // The accounts that stop working if a key or secret goes; undefined until they are known.
  const accountLabels = useProviderAccountLabels(saved?.id);
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [test, setTest] = useState<{ pending: true } | { pending: false; ok: boolean; text: string } | null>(null);

  const info = types.find((t) => t.type === (saved?.type ?? type));
  // The type says which fields its record takes. A type with two shapes (a program on
  // this computer, or an address) shows the fields of the one chosen.
  const shapes = shapesOf(info);
  const [shapeChoice, setShapeChoice] = useState<Shape | null>(null);
  const shape = shapeChoice && shapes.includes(shapeChoice) ? shapeChoice : startShape(info, saved);
  const fields = optionFields(info, shape);
  const addressInfo = addressField(info, shape);
  const [typed, setTyped] = useState<FieldValues>({});
  const values: FieldValues = { ...valuesFromOptions(fields, saved?.options), ...typed };
  const built = optionsFromValues(info, shape, values, saved?.options);
  const optionErrors = !built.ok && showErrors ? built.errors : {};
  const optionsChanged = built.ok ? !sameOptions(built.options, saved?.options) : true;
  const addressShown = shapes.length > 0 ? shape === "address" : Boolean(addressInfo || info?.needs_address || saved?.base_url);
  const addressNeeded = Boolean(info?.needs_address || (shapes.length > 0 && shape === "address"));
  const addressNow = addressShown ? address.trim() || null : null;
  const needsKey = saved ? saved.needs_key : (info?.needs_key ?? false);
  const keyShown = needsKey && (!saved?.has_key || replacingKey);
  const labelError = showErrors ? labelProblem(label) : null;
  const addressError = showErrors && addressShown && addressNeeded && !address.trim() ? `${info?.label ?? "This provider"} needs an address.` : null;
  const idError = !editing && id ? providerIdProblem(id, providers.map((p) => p.id)) : null;
  const keyError = keyShown && key.trim() ? keyProblem(key) : null;
  const dirty = !saved || label.trim() !== saved.label || addressNow !== (saved.base_url ?? null) || optionsChanged || key.trim() !== "";
  const missing = saved ? missingSecrets(saved) : [];

  const refresh = () => void client.invalidateQueries({ queryKey: HANDS_KEYS.providers });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setShowErrors(true);
    setProblem(null);
    if (labelProblem(label) || (addressShown && addressNeeded && !address.trim()) || idError || keyError || !info || !built.ok) return;
    setBusy(true);
    let current = saved;
    try {
      if (!current) {
        const body = { type, label: label.trim(), base_url: addressNow, options: built.options, ...(id ? { id } : {}) };
        current = (await handsApi.createProvider(body)).provider;
        setCreated(current);
      } else if (label.trim() !== current.label || addressNow !== (current.base_url ?? null) || optionsChanged) {
        current = (await handsApi.updateProvider(current.id, { label: label.trim(), base_url: addressNow, options: built.options })).provider;
      }
      setTyped({});
      if (keyShown && key.trim()) {
        await handsApi.setKey(current.id, key.trim());
        current = { ...current, has_key: true };
      }
      // The key never stays in the page once it is saved.
      setKey("");
      setReplacingKey(false);
      refresh();
      const still = missingSecrets(current);
      if (still.length > 0) {
        // The record names values that are not saved yet: stay, so they can be saved here.
        toast.success(`${editing ? "Saved" : "Added"} ${current.label}. Now save the ${still.length === 1 ? "value" : "values"} it needs.`);
        setShowErrors(false);
        return;
      }
      toast.success(editing ? `Saved ${current.label}.` : current.needs_key && !current.has_key ? `Added ${current.label}. Save its key before connecting an account.` : `Added ${current.label}. Now connect an account.`);
      onClose();
    } catch (error) {
      setProblem(errorDetail(error));
      refresh();
    } finally {
      setBusy(false);
    }
  };

  const clearKey = async () => {
    if (!saved) return;
    setBusy(true);
    try {
      await handsApi.clearKey(saved.id);
      toast.success(`Removed the key for ${saved.label}. Its accounts stop working until you save another.`);
      refresh();
    } catch (error) {
      toast.error(errorDetail(error));
    } finally {
      setBusy(false);
      setConfirmingKeyRemoval(false);
    }
  };

  const clearSecret = async (secret: string) => {
    if (!saved) return;
    setBusy(true);
    try {
      await handsApi.clearSecret(saved.id, secret);
      toast.success(`Removed the secret "${secret}".`);
      refresh();
    } catch (error) {
      toast.error(errorDetail(error));
    } finally {
      setBusy(false);
      setRemovingSecret(null);
    }
  };

  const runTest = async () => {
    if (!saved) return;
    setTest({ pending: true });
    try {
      const result = await handsApi.check(saved.id);
      setTest({ pending: false, ok: result.ok, text: result.ok ? "It answered. The provider can be reached with the saved key." : (result.error ?? "It could not be reached.") });
    } catch (error) {
      setTest({ pending: false, ok: false, text: errorDetail(error) });
    }
    refresh();
  };

  if (types.length === 0 && !saved) {
    return (
      <Modal title="Add a provider" onClose={onClose}>
        <p className="px-4 py-4 text-sm text-text-muted">No type of provider is installed in this copy of JimmyHQ yet, so there is nothing to add. Providers arrive as connectors are installed.</p>
        <div className="flex justify-end border-t border-border px-4 py-3">
          <button type="button" className="btn btn-primary" onClick={onClose} data-autofocus>
            OK
          </button>
        </div>
      </Modal>
    );
  }

  const status = saved ? healthTone(saved) : null;

  return (
    <>
      <Modal title={editing ? `Configure ${saved.label}` : "Add a provider"} onClose={onClose} widthClass="max-w-lg">
        <form className="flex min-h-0 flex-1 flex-col" onSubmit={(event) => void submit(event)} noValidate>
          <div className="@container flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4">
            {editing ? (
              <p className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
                <span>
                  {info?.label ?? saved.type} · id <span className="font-mono">{saved.id}</span>. The type and id cannot be changed.
                </span>
                {status ? <Pill tone={status.tone}>{status.text}</Pill> : null}
              </p>
            ) : (
              <SelectField
                label="Type"
                value={type}
                hint={typeAbout(info)}
                onChange={(event) => {
                  setType(event.target.value);
                  setProblem(null);
                }}
                data-autofocus
              >
                {types.map((item) => (
                  <option key={item.type} value={item.type}>
                    {item.label}
                  </option>
                ))}
              </SelectField>
            )}
            {editing ? <p className="text-xs text-text-faint">{typeAbout(info)}</p> : null}

            <Field
              label="Name"
              value={label}
              onChange={(event) => {
                setLabel(event.target.value);
                setProblem(null);
              }}
              error={labelError ?? undefined}
              maxLength={80}
              autoComplete="off"
              data-autofocus={editing ? true : undefined}
              hint="What you call it, for example the service's name."
            />

            {shapes.length > 1 ? (
              <SelectField
                label="Where it is"
                value={shape ?? ""}
                onChange={(event) => {
                  setShapeChoice(event.target.value as Shape);
                  setProblem(null);
                }}
              >
                {shapes.map((item) => (
                  <option key={item} value={item}>
                    {SHAPE_LABELS[item]}
                  </option>
                ))}
              </SelectField>
            ) : null}

            {addressShown ? (
              <Field
                label={addressNeeded ? "Address" : (addressInfo?.label ?? "Address (optional)")}
                value={address}
                onChange={(event) => setAddress(event.target.value)}
                error={addressError ?? undefined}
                hint={addressInfo?.help || "Where the provider answers."}
                inputMode="url"
                autoComplete="off"
                spellCheck={false}
              />
            ) : null}

            {fields.map((field) =>
              isList(field) ? (
                <AreaField
                  key={field.name}
                  label={field.label}
                  value={values[field.name] ?? ""}
                  onChange={(event) => setTyped((now) => ({ ...now, [field.name]: event.target.value }))}
                  error={optionErrors[field.name]}
                  hint={field.help || undefined}
                  rows={field.kind === "argv" ? 3 : 2}
                  spellCheck={false}
                  autoComplete="off"
                />
              ) : (
                <Field
                  key={field.name}
                  label={field.label}
                  value={values[field.name] ?? ""}
                  onChange={(event) => setTyped((now) => ({ ...now, [field.name]: event.target.value }))}
                  error={optionErrors[field.name]}
                  hint={field.help || undefined}
                  inputMode={field.kind === "number" ? "decimal" : undefined}
                  autoComplete="off"
                  spellCheck={false}
                />
              ),
            )}

            {needsKey ? (
              saved?.has_key && !replacingKey ? (
                <div className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-text">Key</span>
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border px-3 py-2">
                    <p className="min-w-0 flex-1 basis-40 text-xs text-text-muted">A key is saved. It is never shown again.</p>
                    <button type="button" className={SMALL_BTN} onClick={() => setReplacingKey(true)}>
                      Replace
                    </button>
                    <button type="button" className={SMALL_BTN} onClick={() => setConfirmingKeyRemoval(true)} disabled={busy} aria-label={`Remove the saved key for ${saved.label}`}>
                      Remove
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-1.5">
                  <Field
                    label={replacingKey ? "New key" : editing ? "Key" : "Key (you can add it later)"}
                    type="password"
                    value={key}
                    onChange={(event) => {
                      setKey(event.target.value);
                      setProblem(null);
                    }}
                    error={showErrors && keyError ? keyError : undefined}
                    hint="Saved on this computer in the secret store and never shown again."
                    autoComplete="off"
                    spellCheck={false}
                  />
                  {replacingKey ? (
                    <button
                      type="button"
                      className="self-start rounded text-xs text-accent hover:underline"
                      onClick={() => {
                        setReplacingKey(false);
                        setKey("");
                      }}
                    >
                      Keep the saved key
                    </button>
                  ) : null}
                </div>
              )
            ) : null}

            {missing.length > 0 ? (
              <Notice tone="warn">
                This provider cannot be used until {missing.length === 1 ? "a value is" : "values are"} saved under {missing.length === 1 ? "this name" : "these names"}: <span className="font-mono">{missing.join(", ")}</span>. Save {missing.length === 1 ? "it" : "each"} below. A value is never written in the record and never shown again.
              </Notice>
            ) : null}
            {saved ? <SecretsEditor provider={saved} suggested={missing[0] ?? ""} removingBusy={busy} onRemove={setRemovingSecret} onChanged={refresh} /> : null}

            <p className="text-xs text-text-faint">{secretsSentence(info)}</p>

            {!editing ? (
              <details className="rounded-lg border border-border" open={idError ? true : undefined}>
                <summary className="cursor-pointer rounded-lg px-3 py-2 text-xs font-medium text-text">Advanced</summary>
                <div className="border-t border-border px-3 py-3">
                  <Field
                    label="Id (optional)"
                    value={id}
                    onChange={(event) => setId(event.target.value)}
                    error={idError ?? undefined}
                    hint={`Made from the name if left empty: ${providerSlug(label) || "…"}. It cannot be changed later.`}
                    maxLength={40}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
              </details>
            ) : null}

            {test && !test.pending ? (
              <Notice tone={test.ok ? "good" : "bad"} live>
                {test.text}
              </Notice>
            ) : null}
            {problem ? <Notice tone="bad">{problem}</Notice> : null}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
            {editing ? (
              <button type="button" className="btn mr-auto" onClick={() => void runTest()} disabled={test?.pending === true || busy || dirty} title={dirty ? "Save your changes first: the test uses what is saved." : undefined}>
                {test?.pending ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : null}
                Test
              </button>
            ) : null}
            <button type="button" className="btn" onClick={onClose}>
              {editing && !dirty ? "Close" : "Cancel"}
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy || (editing && !dirty)}>
              {busy ? "Saving…" : editing ? "Save" : "Add provider"}
            </button>
          </div>
        </form>
      </Modal>
      {confirmingKeyRemoval && saved ? (
        <ConfirmDialog
          {...removeKeyQuestion(saved.label, accountLabels)}
          confirmLabel={busy ? "Removing…" : "Remove key"}
          danger
          busy={busy}
          onConfirm={() => void clearKey()}
          onCancel={() => setConfirmingKeyRemoval(false)}
        />
      ) : null}
      {removingSecret && saved ? (
        <ConfirmDialog
          {...removeSecretQuestion(saved.label, removingSecret, (saved.secrets_wanted ?? []).includes(removingSecret), accountLabels)}
          confirmLabel={busy ? "Removing…" : "Remove secret"}
          danger
          busy={busy}
          onConfirm={() => void clearSecret(removingSecret)}
          onCancel={() => setRemovingSecret(null)}
        />
      ) : null}
    </>
  );
}

/** The labels of the accounts connected to a provider, or undefined while they are not known. */
function useProviderAccountLabels(providerId: string | undefined): string[] | undefined {
  const accounts = useAccountsQuery().data?.accounts;
  if (!providerId || !accounts) return undefined;
  return accounts.filter((account) => account.provider_id === providerId).map((account) => account.label);
}

/** The other secrets a provider keeps, such as a signing secret: names are listed, values never are. */
function SecretsEditor({ provider, suggested, removingBusy, onRemove, onChanged }: { provider: Provider; suggested: string; removingBusy: boolean; onRemove: (secret: string) => void; onChanged: () => void }) {
  const toast = useToast();
  // The name box starts with the next value the record still needs, and moves on as each is saved.
  const [nameTyped, setName] = useState<string | null>(null);
  const name = nameTyped ?? suggested;
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [shown, setShown] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    const nameProblem = secretNameProblem(name.trim(), provider.secrets);
    const valueProblem = keyProblem(value);
    if (nameProblem || valueProblem) {
      setError(nameProblem ?? valueProblem);
      return;
    }
    setBusy(true);
    try {
      await handsApi.setSecret(provider.id, name.trim(), value.trim());
      setName(null);
      setValue("");
      setError(null);
      toast.success(`Saved the secret "${name.trim()}". It is never shown again.`);
      onChanged();
    } catch (failure) {
      setError(errorDetail(failure));
    } finally {
      setBusy(false);
    }
  };

  if (provider.secrets.length === 0 && !shown && !suggested) {
    return (
      <button type="button" className="self-start rounded text-xs text-accent hover:underline" onClick={() => setShown(true)}>
        Save another named secret
      </button>
    );
  }

  return (
    <div className="flex flex-col gap-2" role="group" aria-label="Other saved secrets">
      <span className="text-xs font-medium text-text">Other saved secrets</span>
      {provider.secrets.length > 0 ? (
        <ul className="rounded-lg border border-border">
          {provider.secrets.map((secret) => (
            <li key={secret} className="flex items-center gap-2 border-b border-border/55 px-3 py-1.5 last:border-b-0">
              <span className="min-w-0 flex-1 truncate font-mono text-xs">{secret}</span>
              <span className="text-xs text-text-faint">saved, never shown</span>
              <button type="button" className={SMALL_BTN} onClick={() => onRemove(secret)} disabled={busy || removingBusy} aria-label={`Remove the secret ${secret}`}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="grid grid-cols-1 gap-2 @md:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] @md:items-end">
        <Field label="Secret's name" value={name} onChange={(event) => setName(event.target.value)} maxLength={40} autoComplete="off" spellCheck={false} />
        <Field label="Value" type="password" value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" spellCheck={false} />
        <button type="button" className={`${SMALL_BTN} h-9`} onClick={() => void add()} disabled={busy}>
          Save secret
        </button>
      </div>
      {error ? <Notice tone="bad">{error}</Notice> : null}
    </div>
  );
}
