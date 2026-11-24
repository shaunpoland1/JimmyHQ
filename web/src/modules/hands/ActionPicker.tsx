import { Search } from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { ActionInfo, Effect } from "./api";
import { effectsPresent, filterActions } from "./lib/group";
import { EFFECT_LABELS, CAUTIOUS, sourceLabel } from "./lib/wording";
import { Failed, Loading, Tag } from "./ui";

type Props = {
  actions: ActionInfo[] | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  /** Names of actions this agent already has on this account. */
  taken: Set<string>;
  chosen: string | null;
  onChoose: (name: string) => void;
};

/** A searchable list of what one account can do, with what each does, what kind of effect it has and who said so. */
export function ActionPicker({ actions, loading, error, onRetry, taken, chosen, onChoose }: Props) {
  const [query, setQuery] = useState("");
  const [effect, setEffect] = useState<Effect | "all">("all");
  const id = useId();
  const shown = useMemo(() => filterActions(actions ?? [], query, effect), [actions, query, effect]);
  const effects = useMemo(() => effectsPresent(actions ?? []), [actions]);

  if (loading) return <Loading>Asking the provider what this account can do…</Loading>;
  if (error) return <Failed what="The list of actions" error={error} onRetry={onRetry} />;
  if (!actions || actions.length === 0) return <p className="text-sm text-text-muted">This account has no actions to offer. Check that the provider is on, and that the account is still connected.</p>;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 basis-48">
          <label htmlFor={`${id}-search`} className="sr-only">
            Search actions
          </label>
          <Search size={13} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-faint" aria-hidden="true" />
          <input id={`${id}-search`} type="search" className="input pl-8" placeholder="Search actions" value={query} onChange={(event) => setQuery(event.target.value)} autoComplete="off" data-autofocus />
        </div>
        {effects.length > 1 ? (
          <>
            <label htmlFor={`${id}-kind`} className="sr-only">
              Kind of action
            </label>
            <select id={`${id}-kind`} className="input w-auto pr-8" value={effect} onChange={(event) => setEffect(event.target.value as Effect | "all")}>
              <option value="all">All kinds</option>
              {effects.map((item) => (
                <option key={item} value={item}>
                  {EFFECT_LABELS[item]}
                </option>
              ))}
            </select>
          </>
        ) : null}
      </div>
      <p className="text-xs text-text-faint" role="status">
        {shown.length === actions.length ? `${actions.length} ${actions.length === 1 ? "action" : "actions"}.` : `${shown.length} of ${actions.length} match.`}
      </p>
      {shown.length === 0 ? <p className="text-sm text-text-muted">Nothing matches. Clear the search or choose All kinds.</p> : null}
      <ul className="max-h-64 overflow-y-auto rounded-lg border border-border" aria-label="Actions this account can do">
        {shown.map((action) => {
          const already = taken.has(action.name);
          const blocked = !action.can_offer;
          const reason = already ? "Already switched on for this agent." : blocked ? (action.cannot_offer_reason ?? "It cannot be offered yet.") : null;
          const selected = chosen === action.name;
          return (
            <li key={action.name} className="border-b border-border/55 last:border-b-0">
              <button
                type="button"
                className={`flex w-full flex-col gap-1 px-3 py-2 text-left ${selected ? "bg-surface-2" : "hover:bg-surface-2/60"} disabled:cursor-not-allowed`}
                aria-pressed={selected}
                disabled={already}
                onClick={() => onChoose(action.name)}
              >
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className={`min-w-0 break-words text-[13px] font-medium ${blocked || already ? "text-text-muted" : "text-text"}`}>{action.label}</span>
                  <Tag tone={CAUTIOUS.includes(action.effect) ? "warn" : undefined}>{EFFECT_LABELS[action.effect]}</Tag>
                  <span className="text-[11px] text-text-faint">{sourceLabel(action.effect_from)}</span>
                </span>
                {action.description ? <span className="line-clamp-2 break-words text-xs text-text-muted">{action.description}</span> : null}
                {reason ? <span className="break-words text-xs text-warning">{blocked && !already ? `Cannot be offered yet. ${reason} Open it to fix or hide that argument.` : reason}</span> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
