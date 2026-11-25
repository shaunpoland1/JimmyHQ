// The values a request to use an integration would send, shown on its Bench card. The card
// on the Tape names each argument and its size only; the values come from the hands API
// while the request waits, and the card says plainly once they are gone.

import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../../api";
import { handsApi } from "./api";
import { isRequestCard, requestView } from "./lib/request";

const LABEL = "block font-mono text-[10.5px] uppercase tracking-[0.1em] text-text-faint";

/** Renders nothing for any card that is not a request to use an integration. */
export function RequestValues({ cardId, kind, status }: { cardId: string; kind: string; status: string }) {
  const request = isRequestCard(kind);
  const query = useQuery({
    // The card's status is in the key, so a decision reads the request again.
    queryKey: ["hands", "request", cardId, status],
    queryFn: () => handsApi.requestValues(cardId),
    enabled: request,
    retry: false,
    staleTime: 0,
    gcTime: 0,
  });
  if (!request) return null;
  const error = query.error ? (query.error instanceof ApiError ? query.error : new ApiError(0, String(query.error))) : null;
  const view = requestView(query.data, error);

  return (
    <section className="mb-4" aria-live="polite">
      <span className={`${LABEL} mb-[5px]`}>What it would send</span>
      {view.state === "loading" ? (
        <p className="text-sm text-text-faint">Loading what this request would send…</p>
      ) : view.state === "values" ? (
        <dl className="flex flex-col gap-2.5">
          {view.rows.map((row) => (
            <div key={row.name} className="min-w-0">
              <dt className="font-mono text-[11.5px] text-text-muted [overflow-wrap:anywhere]">{row.name}</dt>
              <dd className="mt-1 max-w-[68ch] whitespace-pre-wrap rounded-md border border-border bg-bg px-2.5 py-1.5 font-mono text-[12.5px] leading-[1.55] [overflow-wrap:anywhere]">
                {row.text === "" ? <span className="text-text-faint">(empty)</span> : row.text}
              </dd>
            </div>
          ))}
          {view.rows.length === 0 ? <p className="text-[13px] text-text-muted">This action takes no arguments.</p> : null}
        </dl>
      ) : view.state === "error" ? (
        <div className="flex flex-col items-start gap-2" role="alert">
          <p className="text-[13px] text-text-muted">{view.sentence}</p>
          <button type="button" className="btn h-7 px-2 text-xs" onClick={() => void query.refetch()}>
            Try again
          </button>
        </div>
      ) : (
        <p className="text-[13px] text-text-muted" role="status">
          {view.sentence}
        </p>
      )}
    </section>
  );
}
