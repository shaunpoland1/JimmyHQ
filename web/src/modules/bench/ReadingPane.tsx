import { ArrowLeft, Check, Download, FileText } from "lucide-react";
import { useEffect, useId } from "react";
import type { RefObject } from "react";
import { errorDetail } from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { useAgentNames } from "../../lib/useAgentNames";
import { useCardAppLink } from "../apps/useCardAppLink";
import { fileUrl } from "./api";
import type { Action, Card, CardListItem, SnoozeChoice } from "./api";
import { stillSnoozed, waitStart } from "./lib/board";
import { describeFiler, formatAgo, formatFull, formatWhen, historyLine } from "./lib/format";
import { BodyText, FilerTile, KindPill, Label, Tag } from "./parts";
import { SnoozeMenu } from "./SnoozeMenu";
import { useBenchCard } from "./useBench";

/** What the owner has typed on a card but not sent yet. */
export type Draft = {
  note: string;
  reason: string;
  /** The action waiting on a reason, once its button has been pressed. */
  reasonFor: string | null;
  error: string | null;
};

export const EMPTY_DRAFT: Draft = { note: "", reason: "", reasonFor: null, error: null };

type ReadingPaneProps = {
  cardId: string | null;
  /** The card as the list has it, shown while the full card loads. */
  listItem: CardListItem | undefined;
  now: number;
  draft: Draft;
  onDraft: (patch: Partial<Draft>) => void;
  busy: boolean;
  onAct: (card: CardListItem | Card, action: Action) => void;
  onSnooze: (card: CardListItem | Card, until?: number) => void;
  onPickTime: (card: CardListItem | Card) => void;
  onUnsnooze: (card: CardListItem | Card) => void;
  onBack: () => void;
  snoozeOpen: boolean;
  onSnoozeOpen: (open: boolean) => void;
  snoozeHours: number | undefined;
  snoozeChoices: readonly SnoozeChoice[] | undefined;
  timezone: string | undefined;
  reasonRef: RefObject<HTMLInputElement | null>;
  /** Bumped whenever the reason field should take focus. */
  focusReason: number;
};

const ACTION_STYLES: Record<Action["style"], string> = {
  primary:
    "btn border-success bg-success font-semibold text-accent-fg hover:bg-[color-mix(in_srgb,var(--success)_88%,var(--text))]",
  default: "btn",
  danger: "btn border-[color-mix(in_srgb,var(--danger)_45%,var(--border))] text-danger",
};

const NOTE_LIMIT = 2000;

function Hint({ keys, does }: { keys: string[]; does: string }) {
  return (
    <span className="flex items-center gap-1">
      {keys.map((key) => (
        <kbd key={key} className="rounded border border-border bg-bg px-1 font-mono text-[10.5px] text-text-faint">
          {key}
        </kbd>
      ))}
      {does}
    </span>
  );
}

/** The right pane: one card in full, with its files, history, note and action buttons. */
export function ReadingPane({
  cardId,
  listItem,
  now,
  draft,
  onDraft,
  busy,
  onAct,
  onSnooze,
  onPickTime,
  onUnsnooze,
  onBack,
  snoozeOpen,
  onSnoozeOpen,
  snoozeHours,
  snoozeChoices,
  timezone,
  reasonRef,
  focusReason,
}: ReadingPaneProps) {
  const query = useBenchCard(cardId);
  const names = useAgentNames();
  const noteId = useId();
  const reasonId = useId();
  const full = query.data;
  const card: Card | CardListItem | undefined = full ?? listItem;
  const appLink = useCardAppLink(card?.filed_by ?? "");

  useEffect(() => {
    if (focusReason > 0) reasonRef.current?.focus();
  }, [focusReason, reasonRef]);

  const back = (
    <button type="button" className="btn mb-2.5 h-7 px-2 text-xs @2xl:hidden" onClick={onBack}>
      <ArrowLeft size={14} aria-hidden="true" />
      Back to list
    </button>
  );

  if (cardId === null) {
    return (
      <div className="h-full min-h-[60%]">
        <EmptyState icon={Check} title="All clear" description="Nothing in this lane is waiting on you." />
      </div>
    );
  }

  if (!card) {
    return (
      <div className="px-3.5 pb-6 pt-3.5 @2xl:px-6 @2xl:pb-7 @2xl:pt-[18px]">
        {back}
        {query.isError ? (
          <div className="flex flex-col items-start gap-3" role="alert">
            <p className="text-sm text-text-muted">{errorDetail(query.error)}</p>
            <button type="button" className="btn" onClick={() => void query.refetch()}>
              Try again
            </button>
          </div>
        ) : (
          <p className="text-sm text-text-faint">Loading the card…</p>
        )}
      </div>
    );
  }

  const filer = describeFiler(card.filed_by, names, card.source);
  const open = card.status === "open";
  // A snooze can run out while the card is on screen, and nothing announces it.
  const snoozed = stillSnoozed(card, now);
  const lane = snoozed ? "waiting" : card.lane;
  const canSnooze = open && card.lane === "needs";
  const since = lane === "done" ? card.filed : waitStart(card);
  const reasonAction = open ? card.actions.find((action) => action.id === draft.reasonFor) : undefined;
  const text = full ? full.body || card.summary : card.summary;
  const history = full ? full.history.filter((entry) => entry.type !== "filed") : [];
  const withdrawn = full?.history.findLast((entry) => entry.type === "withdrawn");

  return (
    <div className="max-w-[820px] px-3.5 pb-6 pt-3.5 @2xl:px-6 @2xl:pb-7 @2xl:pt-[18px]">
      {back}
      <div className="flex items-start gap-2.5">
        <h2 className="min-w-0 flex-1 text-base font-semibold leading-[1.35] tracking-[-0.01em] [overflow-wrap:anywhere]">
          {card.title}
        </h2>
        {canSnooze ? (
          <div className="flex flex-none flex-wrap items-center justify-end gap-1.5">
            {snoozed ? (
              <button type="button" className="btn h-7 px-2 text-xs" disabled={busy} onClick={() => onUnsnooze(card)}>
                Unsnooze
              </button>
            ) : null}
            <SnoozeMenu
              open={snoozeOpen}
              onOpenChange={onSnoozeOpen}
              now={now}
              defaultHours={snoozeHours}
              choices={snoozeChoices}
              timezone={timezone}
              snoozed={snoozed}
              disabled={busy}
              onSnooze={(until) => onSnooze(card, until)}
              onPickTime={() => onPickTime(card)}
            />
          </div>
        ) : null}
      </div>

      <div className="mb-[18px] mt-2 flex flex-wrap items-center gap-2 font-mono text-[11.5px] text-text-muted">
        <Tag>{card.source}</Tag>
        <KindPill kind={card.kind} lane={lane} />
        <span className="flex min-w-0 items-center gap-1.5">
          filed by <FilerTile filedBy={card.filed_by} source={card.source} />
          <b className="truncate font-semibold text-text">{filer.name}</b>
        </span>
        <span title={formatFull(since)}>· {formatAgo(now - since)}</span>
        {snoozed && card.snoozed_until !== null ? (
          <span title={formatFull(card.snoozed_until)}>· snoozed until {formatWhen(card.snoozed_until, now)}</span>
        ) : null}
      </div>

      {full && !open ? (
        <div className="mb-4 rounded-lg border border-border bg-bg px-3 py-2.5 text-[13px]" role="status">
          {full.status === "decided" && full.decision ? (
            <>
              <p>
                <b className="font-semibold">{full.decision.label}</b>
                <span className="text-text-muted"> · decided {formatFull(full.decision.ts)}</span>
              </p>
              {full.decision.reason ? <BodyText text={`Reason: ${full.decision.reason}`} className="mt-1 text-text-muted" /> : null}
              {full.decision.note ? <BodyText text={`Note: ${full.decision.note}`} className="mt-1 text-text-muted" /> : null}
            </>
          ) : full.status === "withdrawn" ? (
            <>
              <p>
                <b className="font-semibold">Withdrawn</b>
                <span className="text-text-muted">
                  {withdrawn ? ` · by ${describeFiler(withdrawn.actor, names).name}, ${formatFull(withdrawn.ts)}` : ""}
                </span>
              </p>
              {withdrawn?.type === "withdrawn" && withdrawn.note ? <BodyText text={withdrawn.note} className="mt-1 text-text-muted" /> : null}
            </>
          ) : full.status === "expired" ? (
            <p>
              <b className="font-semibold">Expired</b>
              <span className="text-text-muted"> · nobody dismissed it, so it closed by itself.</span>
            </p>
          ) : (
            <p className="font-semibold">Closed</p>
          )}
        </div>
      ) : null}

      {text ? (
        <section className="mb-4">
          <Label className="mb-[5px]">What it says</Label>
          <BodyText text={text} />
        </section>
      ) : null}

      {!full ? (
        query.isError ? (
          <div className="mb-4 flex flex-col items-start gap-3" role="alert">
            <p className="text-sm text-text-muted">{errorDetail(query.error)}</p>
            <button type="button" className="btn" onClick={() => void query.refetch()}>
              Try again
            </button>
          </div>
        ) : (
          <p className="mb-4 text-sm text-text-faint">Loading the card…</p>
        )
      ) : (
        <>
          {full.body_missing ? (
            <p className="mb-4 text-[13px] text-warning" role="status">
              The full text of this card could not be read from the Vault.
            </p>
          ) : null}

          {full.why ? (
            <section className="mb-4">
              <Label className="mb-[5px]">Why it matters</Label>
              <BodyText text={full.why} className="border-l-2 border-sage pl-3 text-text-muted" />
            </section>
          ) : null}

          {full.details.map((detail, index) => (
            <section key={index} className="mb-4">
              <Label className="mb-[5px] [overflow-wrap:anywhere]">{detail.label}</Label>
              <BodyText text={detail.text} />
            </section>
          ))}

          {full.files.length > 0 ? (
            <section className="mb-4">
              <Label className="mb-[5px]">Files · download</Label>
              <ul className="flex flex-wrap gap-1.5">
                {full.files.map((file) => (
                  <li key={file.index} className="max-w-full">
                    <a
                      href={fileUrl(full.id, file.index)}
                      download={file.name}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-bg px-2 py-1 font-mono text-[11.5px] text-text-muted hover:border-sage hover:text-text"
                      aria-label={`Download ${file.name}`}
                    >
                      <FileText size={13} className="flex-none" aria-hidden="true" />
                      <span className="truncate">{file.name}</span>
                      <Download size={12} className="flex-none text-text-faint" aria-hidden="true" />
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {history.length > 0 ? (
            <section className="mb-4">
              <Label className="mb-[5px]">History</Label>
              <ol className="flex flex-col gap-1.5 text-[13px]">
                {history.map((entry, index) => {
                  const line = historyLine(entry, now, undefined, names);
                  return (
                    <li key={index} className="min-w-0">
                      <div className="flex flex-wrap items-baseline gap-x-2">
                        <span className="[overflow-wrap:anywhere]">{line.text}</span>
                        <span className="font-mono text-[11px] text-text-faint" title={formatFull(entry.ts)}>
                          {formatAgo(now - entry.ts)}
                        </span>
                      </div>
                      {line.extra.map((extra, at) => (
                        <BodyText key={at} text={extra} className="border-l-2 border-border pl-3 text-text-muted" />
                      ))}
                    </li>
                  );
                })}
              </ol>
            </section>
          ) : null}
        </>
      )}

      {open ? (
        <>
          <div>
            <Label htmlFor={noteId}>{filer.kind === "owner" ? "Note · optional" : `Note for ${filer.name} · optional`}</Label>
            <input
              id={noteId}
              className="input mb-3 mt-1 h-8 bg-bg"
              placeholder="One line, sent with your decision"
              maxLength={NOTE_LIMIT}
              value={draft.note}
              onChange={(event) => onDraft({ note: event.target.value })}
            />
          </div>

          {reasonAction ? (
            <div className="mb-3">
              <Label htmlFor={reasonId}>Reason for {reasonAction.label} · required</Label>
              <input
                id={reasonId}
                ref={reasonRef}
                className="input mt-1 h-8 bg-bg"
                placeholder="Say why in a sentence"
                maxLength={NOTE_LIMIT}
                required
                aria-required="true"
                aria-invalid={draft.error ? true : undefined}
                aria-describedby={`${reasonId}-help`}
                value={draft.reason}
                onChange={(event) => onDraft({ reason: event.target.value, error: null })}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    onAct(card, reasonAction);
                  } else if (event.key === "Escape") {
                    event.preventDefault();
                    onDraft({ reasonFor: null, error: null });
                  }
                }}
              />
              <p id={`${reasonId}-help`} className={`mt-1 text-xs ${draft.error ? "text-danger" : "text-text-faint"}`} role={draft.error ? "alert" : undefined}>
                {draft.error ?? `Say why, then press Enter or ${reasonAction.label} again. Escape cancels.`}
              </p>
            </div>
          ) : null}

          <div
            className="flex flex-wrap items-center gap-2"
            onKeyDown={(event) => {
              // Holding Enter on a button must not decide one card after another.
              if (event.repeat && (event.key === "Enter" || event.key === " ")) event.preventDefault();
            }}
          >
            {card.actions.map((action) => (
              <button
                key={action.id}
                type="button"
                className={ACTION_STYLES[action.style]}
                disabled={busy}
                onClick={() => onAct(card, action)}
              >
                {action.label}
              </button>
            ))}
            {appLink ? (
              <a className="btn" href={appLink.href}>
                {appLink.label}
              </a>
            ) : null}
            {reasonAction ? (
              <button
                type="button"
                className="btn border-transparent bg-transparent text-text-muted"
                onClick={() => onDraft({ reasonFor: null, error: null })}
              >
                Cancel
              </button>
            ) : null}
          </div>
        </>
      ) : null}

      <div className="mt-[18px] hidden flex-wrap gap-3 border-t border-border pt-3 font-mono text-[11px] text-text-faint @2xl:flex">
        <Hint keys={["j", "k"]} does="move" />
        <Hint keys={["a"]} does="main action" />
        <Hint keys={["n"]} does="not yet" />
        <Hint keys={["d"]} does="no" />
        <Hint keys={["s"]} does="snooze" />
        <Hint keys={["?"]} does="all keys" />
      </div>
    </div>
  );
}
