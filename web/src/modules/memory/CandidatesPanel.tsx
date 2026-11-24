import { Check, FileText, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { errorDetail } from "../../api";
import { useToast } from "../../hooks/useToast";
import { formatHash } from "../../lib/route";
import { memoryApi } from "./api";
import type { Candidate } from "./api";
import { useCandidateActions, useCandidates, useSources } from "./hooks";
import type { CandidateActions } from "./hooks";
import {
  DEFAULT_VIEW,
  MAX_REASON,
  REASON_NEEDED,
  VIEWS,
  canDecline,
  canKeep,
  checkReason,
  confidenceNumber,
  countsOf,
  flattenCandidates,
  isView,
  quoteAuthor,
  stateWords,
  uniqueCandidates,
  viewCount,
} from "./lib/candidates";
import type { CandidateView } from "./lib/candidates";
import { sourceLabels } from "./lib/mining";
import { Confidence, Label, Loading, QueryError, When } from "./parts";

/**
 * Facts mining found that wait for the owner: those with a card on the Bench
 * and those saved automatically and not yet confirmed. The other states are a
 * filter away.
 */
export function CandidatesPanel() {
  const [view, setView] = useState<CandidateView>(DEFAULT_VIEW);
  const actions = useCandidateActions();
  const sources = useSources();
  const labels = sourceLabels(sources.data?.sources);
  const headingId = useId();
  const filterId = useId();

  // "Needs you" is two of the server's lists: the Bench's (always one page) and those to review.
  const first = useCandidates(view === "needs" ? "bench" : view);
  const second = useCandidates("review", view === "needs");
  const lists = view === "needs" ? [first, second] : [first];
  // The list that can have more pages.
  const paged = view === "needs" ? second : first;

  const counts = countsOf(first.data) ?? countsOf(second.data);
  const candidates = uniqueCandidates(lists.map((list) => flattenCandidates(list.data)));
  const loading = lists.some((list) => !list.data && !list.isError);
  const failed = lists.find((list) => list.isError);
  const needs = viewCount(counts, "needs");
  const empty = VIEWS.find((item) => item.id === view)?.empty ?? "";

  return (
    <section aria-labelledby={headingId} className="@container rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border px-3 py-2">
        <Label id={headingId} className="flex-none">
          Found for you
        </Label>
        {needs > 0 ? (
          <span className="chip" title="On the Bench, or saved automatically and not confirmed">
            {needs} <span className="sr-only">need you</span>
          </span>
        ) : null}
        <label htmlFor={filterId} className="sr-only">
          Which proposed facts to list
        </label>
        <select
          id={filterId}
          className="input ml-auto h-7 w-auto min-w-0 max-w-full flex-1 basis-40 py-0 pl-2 pr-1 text-xs @sm:flex-none"
          value={view}
          onChange={(event) => {
            if (isView(event.target.value)) setView(event.target.value);
          }}
        >
          {VIEWS.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label} ({viewCount(counts, item.id)})
            </option>
          ))}
        </select>
      </div>

      {failed && candidates.length === 0 ? (
        <QueryError error={failed.error} onRetry={() => lists.forEach((list) => void list.refetch())} className="!px-3" />
      ) : loading && candidates.length === 0 ? (
        <Loading what="proposed facts" />
      ) : candidates.length === 0 ? (
        <p className="px-3 py-3 text-[13px] leading-snug text-text-faint">{empty}</p>
      ) : (
        <>
          {failed ? <QueryError error={failed.error} onRetry={() => lists.forEach((list) => void list.refetch())} className="!px-3" /> : null}
          <ul aria-label="Proposed facts">
            {candidates.map((candidate) => (
              <CandidateItem key={candidate.id} candidate={candidate} source={labels[candidate.source] ?? candidate.source} actions={actions} />
            ))}
          </ul>
          {paged.hasNextPage ? (
            <div className="border-t border-border px-3 py-2">
              <button type="button" className="btn h-7 w-full text-xs" disabled={paged.isFetchingNextPage} onClick={() => void paged.fetchNextPage()}>
                {paged.isFetchingNextPage ? "Loading…" : "Show more"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function CandidateItem({ candidate, source, actions }: { candidate: Candidate; source: string; actions: CandidateActions }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  // null until Decline is pressed; then the reason being typed.
  const [reason, setReason] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reasonBox = useRef<HTMLInputElement>(null);
  const reasonId = useId();

  useEffect(() => {
    if (reason !== null && error !== null) reasonBox.current?.focus();
  }, [reason, error]);

  const run = async (work: () => Promise<boolean>) => {
    if (busy) return;
    setBusy(true);
    try {
      await work();
    } finally {
      setBusy(false);
    }
  };

  const decline = () => {
    if (reason === null) {
      // First press: ask why. The field takes focus as it appears.
      setReason("");
      setError(null);
      return;
    }
    const checked = checkReason(reason);
    if (!checked.ok) {
      setError(checked.error);
      reasonBox.current?.focus();
      return;
    }
    void run(async () => {
      const ok = await actions.decline(candidate, checked.reason);
      // The field stays open with what was typed if the server refuses.
      if (ok) setReason(null);
      return ok;
    });
  };

  const openNote = async () => {
    if (!candidate.fact_id) return;
    try {
      const { fact } = await memoryApi.fact(candidate.fact_id);
      window.location.hash = formatHash({ screen: "memory", sub: fact.note });
    } catch (failure) {
      toast.error(errorDetail(failure));
    }
  };

  const erased = candidate.statement === "";
  const keepable = canKeep(candidate);
  const declinable = canDecline(candidate);

  return (
    <li className="border-b border-border px-3 py-2.5 last:border-b-0">
      {erased ? (
        <p className="text-[13px] italic leading-snug text-text-faint">Its words were erased with "forget everywhere".</p>
      ) : (
        <p className="whitespace-pre-wrap text-[13px] font-medium leading-snug text-text [overflow-wrap:anywhere]">{candidate.statement}</p>
      )}

      <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11.5px] text-text-muted">
        <span className="chip" title="Kind">
          {candidate.kind}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Confidence value={candidate.confidence} />
          <span className="font-mono text-[11px] text-text-faint" title="Confidence, from 0 to 1">
            {confidenceNumber(candidate.confidence)}
          </span>
        </span>
        <span>
          from {source}, <When ts={candidate.proposed_ms} />
        </span>
      </div>
      <p className="mt-1 text-[11.5px] text-text-faint">
        {stateWords(candidate)}
        {candidate.state_ms > candidate.proposed_ms ? (
          <>
            {" "}
            since <When ts={candidate.state_ms} />
          </>
        ) : null}
        {candidate.reason ? <span className="[overflow-wrap:anywhere]">. Reason: {candidate.reason}</span> : null}
      </p>

      {candidate.quotes.length > 0 ? (
        <ul aria-label="What it rests on" className="mt-2 flex flex-col gap-1.5">
          {candidate.quotes.map((quote, index) => (
            <li key={`${quote.ref}-${index}`} className="border-l-2 border-border pl-2.5">
              <p className="whitespace-pre-wrap text-xs leading-snug text-text-muted [overflow-wrap:anywhere]">“{quote.quote}”</p>
              <p className="mt-0.5 text-[11px] text-text-faint [overflow-wrap:anywhere]">
                {quoteAuthor(quote.who)}, <When ts={quote.when_ms} />, {quote.where || "unknown place"}
              </p>
            </li>
          ))}
        </ul>
      ) : !erased ? (
        <p className="mt-2 text-[11.5px] text-text-faint">No quote was kept with it.</p>
      ) : null}

      {reason !== null ? (
        <div className="mt-2">
          <label htmlFor={reasonId} className="text-xs font-medium text-text">
            Reason for declining · required
          </label>
          <input
            id={reasonId}
            ref={reasonBox}
            className="input mt-1 h-8"
            value={reason}
            maxLength={MAX_REASON}
            required
            autoFocus
            autoComplete="off"
            aria-invalid={error ? true : undefined}
            aria-describedby={`${reasonId}-help`}
            onChange={(event) => {
              setReason(event.target.value);
              setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                decline();
              } else if (event.key === "Escape") {
                event.preventDefault();
                event.stopPropagation();
                setReason(null);
                setError(null);
              }
            }}
          />
          <p id={`${reasonId}-help`} className={`mt-1 text-xs ${error ? "text-danger" : "text-text-faint"}`} role={error ? "alert" : undefined}>
            {error ?? `${REASON_NEEDED} Then press Enter or Decline again. Escape cancels.`}
          </p>
        </div>
      ) : null}

      {keepable || declinable || candidate.fact_id ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {keepable && reason === null ? (
            <button
              type="button"
              className="btn btn-primary h-7 px-2.5 text-xs"
              disabled={busy}
              aria-label={candidate.needs_review ? "Keep: confirm this saved fact" : "Keep: save this as a confirmed fact"}
              onClick={() => void run(() => actions.keep(candidate))}
            >
              <Check size={13} aria-hidden="true" />
              Keep
            </button>
          ) : null}
          {declinable ? (
            <button
              type="button"
              className="btn h-7 px-2.5 text-xs"
              disabled={busy}
              aria-label={reason === null ? "Decline: a reason is asked for next" : "Decline with this reason"}
              onClick={decline}
            >
              <X size={13} aria-hidden="true" />
              Decline
            </button>
          ) : null}
          {reason !== null ? (
            <button
              type="button"
              className="btn h-7 px-2.5 text-xs"
              onClick={() => {
                setReason(null);
                setError(null);
              }}
            >
              Cancel
            </button>
          ) : null}
          {candidate.fact_id && candidate.state === "saved" && reason === null ? (
            <button type="button" className="ml-auto flex items-center gap-1 rounded text-[11.5px] text-accent hover:underline" onClick={() => void openNote()}>
              <FileText size={12} aria-hidden="true" />
              Open its note
            </button>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
