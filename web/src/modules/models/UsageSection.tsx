import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useId, useRef, useState } from "react";
import { api, errorDetail } from "../../api";
import { SETTINGS_QUERY_KEY } from "../../hooks/useSettings";
import { useToast } from "../../hooks/useToast";
import { modelsApi } from "./api";
import type { PriceEntry, PricesResponse, Provider, UsageModelRow, UsageResponse, UsageWindow } from "./api";
import { formatCompact, formatCostCell } from "./lib/format";
import { MAX_PRICE_LINES, buildPrices, newDraft, priceErrors, priceLabel, pricesChanged, toDrafts } from "./lib/prices";
import type { PriceDraft, PriceErrors } from "./lib/prices";
import { moveItem, removeAt, replaceAt } from "./lib/roles";
import { MODEL_KEYS, usePricesQuery, useUsageQuery } from "./queries";
import { ErrorCard, LoadingCard, SMALL_BTN, SectionHeading, Tag } from "./ui";

const SAVINGS_SETTING = "models.savings_reference";
const PROVIDER_TYPES = ["openai_compatible", "openai", "anthropic", "google", "bedrock"];

type WindowKey = "last_24h" | "all_time";
const WINDOWS: readonly { key: WindowKey; label: string }[] = [
  { key: "last_24h", label: "Last 24 hours" },
  { key: "all_time", label: "All time" },
];

function count(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function Totals({ usage }: { usage: UsageResponse }) {
  const money = (amount: number) => formatCostCell(amount, usage.currency);
  const rows: readonly { label: string; value: (window: UsageWindow) => string; title?: (window: UsageWindow) => string }[] = [
    { label: "Calls", value: (window) => count(window.calls) },
    { label: "Tokens in", value: (window) => formatCompact(window.input_tokens), title: (window) => count(window.input_tokens) },
    { label: "Tokens out", value: (window) => formatCompact(window.output_tokens), title: (window) => count(window.output_tokens) },
    { label: "Estimated cost", value: (window) => money(window.estimated_cost) },
    { label: "Saved by running locally", value: (window) => (window.saved_by_local === null ? "—" : money(window.saved_by_local)) },
    { label: "Answered by a fallback", value: (window) => count(window.fallback_calls) },
    { label: "Failed attempts", value: (window) => count(window.failed_calls) },
  ];
  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="border-b border-border/55 font-mono text-[10.5px] uppercase tracking-widest text-text-faint">
          <th scope="col" className="px-3 py-2 font-normal">
            <span className="sr-only">Figure</span>
          </th>
          {WINDOWS.map((window) => (
            <th key={window.key} scope="col" className="px-3 py-2 text-right font-normal">
              {window.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className="border-b border-border/55 last:border-b-0">
            <th scope="row" className="px-3 py-1.5 font-normal text-text-muted">
              {row.label}
            </th>
            {WINDOWS.map((window) => (
              <td key={window.key} className="px-3 py-1.5 text-right font-mono text-text" title={row.title?.(usage[window.key])}>
                {row.value(usage[window.key])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function modelCost(row: UsageModelRow, currency: string): string {
  if (row.estimated_cost === null) return "no price";
  if (row.calls > 0 && row.local_calls === row.calls) return "local";
  return formatCostCell(row.estimated_cost, currency);
}

function TopModels({ usage, providers }: { usage: UsageResponse; providers: readonly Provider[] }) {
  const [windowKey, setWindowKey] = useState<WindowKey>("all_time");
  const rows = usage[windowKey].models;
  const labelOf = (row: UsageModelRow) => providers.find((provider) => provider.id === row.provider_id)?.label ?? row.provider_id;
  return (
    <div className="rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/55 px-3 py-2">
        <h4 className="text-xs font-semibold">Top models</h4>
        <div className="ml-auto flex gap-1" role="group" aria-label="Period for top models">
          {WINDOWS.map((window) => (
            <button
              key={window.key}
              type="button"
              aria-pressed={windowKey === window.key}
              onClick={() => setWindowKey(window.key)}
              className={`rounded-md px-2 py-0.5 text-[11px] ${windowKey === window.key ? "bg-surface-2 font-medium text-text ring-1 ring-border" : "text-text-muted hover:text-text"}`}
            >
              {window.label}
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="px-3 py-4 text-xs text-text-faint">No calls {windowKey === "last_24h" ? "in the last 24 hours" : "yet"}.</p>
      ) : (
        <ol>
          {rows.map((row) => {
            const other = row.provider_id === "" && row.model === "";
            return (
              <li key={`${row.provider_id}\n${row.model}`} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-border/55 px-3 py-1.5 last:border-b-0">
                <span className="min-w-0 flex-1 basis-40">
                  <span className={`break-all text-xs text-text ${other ? "" : "font-mono"}`}>{other ? "Other models" : row.model}</span>
                  {other ? null : <span className="ml-2 break-words text-xs text-text-muted">{labelOf(row)}</span>}
                </span>
                <span className="flex flex-none gap-3 font-mono text-[11px] text-text-muted">
                  <span>
                    {formatCompact(row.calls)} {row.calls === 1 ? "call" : "calls"}
                  </span>
                  <span title={`${count(row.input_tokens)} in, ${count(row.output_tokens)} out`}>{formatCompact(row.input_tokens + row.output_tokens)} tok</span>
                  <span className="min-w-14 text-right text-text">{modelCost(row, usage.currency)}</span>
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

function SavingsReference({ usage, prices }: { usage: UsageResponse; prices: readonly PriceEntry[] }) {
  const client = useQueryClient();
  const toast = useToast();
  const id = useId();
  const current = usage.savings_reference?.id ?? "";
  const save = useMutation({
    mutationFn: (value: string) => api.putSetting(SAVINGS_SETTING, value),
    onMutate: async (value) => {
      await client.cancelQueries({ queryKey: MODEL_KEYS.usage });
      const previous = client.getQueryData<UsageResponse>(MODEL_KEYS.usage);
      const reference = prices.find((entry) => entry.id === value) ?? null;
      client.setQueryData<UsageResponse>(MODEL_KEYS.usage, (data) => (data ? { ...data, savings_reference: reference } : data));
      return { previous };
    },
    onError: (error, _value, context) => {
      if (context?.previous) client.setQueryData(MODEL_KEYS.usage, context.previous);
      toast.error(errorDetail(error));
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: MODEL_KEYS.usage });
      void client.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
    },
  });
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-surface px-3 py-3 @xl:flex-row @xl:items-start @xl:justify-between @xl:gap-6">
      <div className="min-w-0 flex-1">
        <label htmlFor={id} className="text-[13px] font-medium text-text">
          Compare local work against
        </label>
        <p id={`${id}-help`} className="mt-0.5 text-xs text-text-muted">
          Work done on your own machines is priced at this line to show what running locally saved. Choose none to hide the figure.
        </p>
      </div>
      <select id={id} className="input w-full pr-8 @xl:w-64 @xl:flex-none" aria-describedby={`${id}-help`} value={current} onChange={(event) => save.mutate(event.target.value)}>
        <option value="">None</option>
        {prices.map((entry) => (
          <option key={entry.id} value={entry.id}>
            {priceLabel(entry)}
          </option>
        ))}
      </select>
    </div>
  );
}

const PRICE_GRID = "grid-cols-2 gap-2 @2xl:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_6.5rem_6.5rem_auto] @2xl:items-start";
const CELL_LABEL = "font-mono text-[10.5px] uppercase tracking-widest text-text-faint @2xl:sr-only";

function PriceTable({ data, currency, providers }: { data: PricesResponse; currency: string; providers: readonly Provider[] }) {
  const client = useQueryClient();
  const toast = useToast();
  const listId = useId();
  // null = showing what is saved; a list = the owner's unsaved edits.
  const [edits, setEdits] = useState<PriceDraft[] | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);
  const sequence = useRef(0);

  const drafts = edits ?? toDrafts(data.prices);
  const dirty = edits !== null && pricesChanged(edits, data.prices);
  const errors: PriceErrors[] = drafts.map(priceErrors);

  const change = (next: PriceDraft[]) => {
    setEdits(next);
    setServerError(null);
  };
  const patch = (index: number, fields: Partial<PriceDraft>) => {
    const draft = drafts[index];
    if (draft) change(replaceAt(drafts, index, { ...draft, ...fields }));
  };

  const save = useMutation({
    mutationFn: (prices: PriceEntry[]) => modelsApi.setPrices(prices),
    onSuccess: (saved) => {
      client.setQueryData<PricesResponse>(MODEL_KEYS.prices, saved);
      void client.invalidateQueries({ queryKey: MODEL_KEYS.usage });
      setEdits(null);
      setShowErrors(false);
      toast.success("Saved the price table.");
    },
    onError: (error) => setServerError(errorDetail(error)),
  });

  const submit = () => {
    setShowErrors(true);
    setServerError(null);
    const built = buildPrices(drafts);
    if (built.ok) save.mutate(built.prices);
  };

  const built = showErrors ? buildPrices(drafts) : null;
  const summary = serverError ?? (built && !built.ok ? built.summary : null);
  const suggestions = ["*", ...PROVIDER_TYPES, ...providers.map((provider) => provider.id)];

  return (
    <div className="rounded-xl border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/55 px-3 py-2">
        <h4 className="text-xs font-semibold">Prices</h4>
        <Tag>per million tokens · {currency || "no currency set"}</Tag>
        {data.is_default ? <Tag title="Shipped examples, not kept up to date">examples</Tag> : null}
      </div>
      <p className="border-b border-border/55 px-3 py-2 text-xs text-text-muted">
        The first line that matches a call prices it. Provider is a provider type (<span className="font-mono">{PROVIDER_TYPES.join(", ")}</span>), one provider's id, or{" "}
        <span className="font-mono">*</span> for any. In a model pattern <span className="font-mono">*</span> matches anything.
        {data.is_default ? " These are example prices: saving makes the table yours." : ""}
      </p>
      <datalist id={listId}>
        {suggestions.map((value) => (
          <option key={value} value={value} />
        ))}
      </datalist>
      <div className={`hidden border-b border-border/55 px-3 py-2 font-mono text-[10.5px] uppercase tracking-widest text-text-faint @2xl:grid ${PRICE_GRID}`} aria-hidden="true">
        <span>Provider</span>
        <span>Model pattern</span>
        <span>Input</span>
        <span>Output</span>
        <span className="w-[5.25rem]" />
      </div>
      {drafts.length === 0 ? <p className="px-3 py-4 text-xs text-text-faint">No price lines. Hosted calls are counted but not priced.</p> : null}
      <ol>
        {drafts.map((draft, index) => {
          const problems = showErrors ? (errors[index] ?? {}) : {};
          const line = `Price line ${index + 1}`;
          const messages = Object.values(problems);
          return (
            <li key={draft.key} className={`grid border-b border-border/55 px-3 py-2 last:border-b-0 ${PRICE_GRID}`}>
              <label className="flex min-w-0 flex-col gap-1">
                <span className={CELL_LABEL}>Provider</span>
                <input
                  className="input font-mono"
                  list={listId}
                  value={draft.provider}
                  onChange={(event) => patch(index, { provider: event.target.value })}
                  aria-label={`${line}: provider`}
                  aria-invalid={problems.provider ? true : undefined}
                  maxLength={40}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
              <label className="flex min-w-0 flex-col gap-1">
                <span className={CELL_LABEL}>Model pattern</span>
                <input
                  className="input font-mono"
                  value={draft.model}
                  onChange={(event) => patch(index, { model: event.target.value })}
                  aria-label={`${line}: model pattern`}
                  aria-invalid={problems.model ? true : undefined}
                  maxLength={200}
                  autoComplete="off"
                  spellCheck={false}
                />
              </label>
              <label className="flex min-w-0 flex-col gap-1">
                <span className={CELL_LABEL}>Input</span>
                <input
                  className="input text-right font-mono"
                  value={draft.input}
                  onChange={(event) => patch(index, { input: event.target.value })}
                  aria-label={`${line}: price per million input tokens`}
                  aria-invalid={problems.input ? true : undefined}
                  inputMode="decimal"
                  autoComplete="off"
                />
              </label>
              <label className="flex min-w-0 flex-col gap-1">
                <span className={CELL_LABEL}>Output</span>
                <input
                  className="input text-right font-mono"
                  value={draft.output}
                  onChange={(event) => patch(index, { output: event.target.value })}
                  aria-label={`${line}: price per million output tokens`}
                  aria-invalid={problems.output ? true : undefined}
                  inputMode="decimal"
                  autoComplete="off"
                />
              </label>
              <div className="col-span-2 flex items-center justify-end @2xl:col-span-1 @2xl:pt-1">
                <button type="button" className="icon-btn" aria-label={`Move ${line.toLowerCase()} up`} disabled={index === 0} onClick={() => change(moveItem(drafts, index, -1))}>
                  <ArrowUp size={14} />
                </button>
                <button type="button" className="icon-btn" aria-label={`Move ${line.toLowerCase()} down`} disabled={index === drafts.length - 1} onClick={() => change(moveItem(drafts, index, 1))}>
                  <ArrowDown size={14} />
                </button>
                <button type="button" className="icon-btn" aria-label={`Remove ${line.toLowerCase()}`} onClick={() => change(removeAt(drafts, index))}>
                  <Trash2 size={14} />
                </button>
              </div>
              {messages.length > 0 ? (
                <p className="col-span-2 text-xs text-danger @2xl:col-span-5" role="alert">
                  {[...new Set(messages)].join(" ")}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap items-center gap-2 border-t border-border/55 px-3 py-2.5">
        <button
          type="button"
          className={SMALL_BTN}
          disabled={drafts.length >= MAX_PRICE_LINES}
          onClick={() => {
            sequence.current += 1;
            change([...drafts, newDraft(sequence.current)]);
          }}
        >
          <Plus size={13} aria-hidden="true" />
          Add line
        </button>
        {summary ? (
          <p className="min-w-0 flex-1 basis-40 break-words text-xs text-danger" role="alert">
            {summary}
          </p>
        ) : (
          <span className="flex-1" />
        )}
        {dirty ? (
          <button
            type="button"
            className={SMALL_BTN}
            onClick={() => {
              setEdits(null);
              setShowErrors(false);
              setServerError(null);
            }}
            disabled={save.isPending}
          >
            Discard changes
          </button>
        ) : null}
        <button type="button" className={`${SMALL_BTN} btn-primary`} onClick={submit} disabled={save.isPending || (!dirty && !data.is_default)}>
          {save.isPending ? "Saving…" : dirty || !data.is_default ? "Save prices" : "Keep these prices"}
        </button>
      </div>
    </div>
  );
}

/** Usage totals, the top models, the savings reference and the price table. */
export function UsageSection({ providers }: { providers: readonly Provider[] | undefined }) {
  const usage = useUsageQuery();
  const prices = usePricesQuery();
  const known = providers ?? [];

  return (
    <section aria-labelledby="models-usage-heading" className="flex flex-col gap-3">
      <div className="-mb-1">
        <SectionHeading id="models-usage-heading">Usage and prices</SectionHeading>
      </div>
      {usage.isPending ? (
        <LoadingCard>Loading usage…</LoadingCard>
      ) : usage.isError ? (
        <ErrorCard what="Usage" error={usage.error} onRetry={() => void usage.refetch()} />
      ) : (
        <>
          <div className="overflow-hidden rounded-xl border border-border bg-surface">
            <Totals usage={usage.data} />
            {usage.data.all_time.calls === 0 ? <p className="border-t border-border/55 px-3 py-2 text-xs text-text-faint">No model has been called yet.</p> : null}
            {usage.data.all_time.unpriced_calls > 0 ? (
              <p className="border-t border-border/55 px-3 py-2 text-xs text-warning">
                {count(usage.data.all_time.unpriced_calls)} hosted {usage.data.all_time.unpriced_calls === 1 ? "call has" : "calls have"} no matching price line and {usage.data.all_time.unpriced_calls === 1 ? "is" : "are"} left out of the estimated cost. Add a line below to price{" "}
                {usage.data.all_time.unpriced_calls === 1 ? "it" : "them"}.
              </p>
            ) : null}
          </div>
          <TopModels usage={usage.data} providers={known} />
          {prices.data ? <SavingsReference usage={usage.data} prices={prices.data.prices} /> : null}
        </>
      )}
      {prices.isPending ? (
        <LoadingCard>Loading prices…</LoadingCard>
      ) : prices.isError ? (
        <ErrorCard what="Prices" error={prices.error} onRetry={() => void prices.refetch()} />
      ) : (
        <PriceTable data={prices.data} currency={usage.data?.currency ?? ""} providers={known} />
      )}
    </section>
  );
}
