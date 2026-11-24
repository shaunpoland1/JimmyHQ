import { Check, ChevronDown, ChevronRight, Clock, Inbox, Paperclip } from "lucide-react";
import type { ReactNode } from "react";
import { EmptyState } from "../../components/EmptyState";
import { plainKind } from "../../lib/plainWords";
import { useAgentNames } from "../../lib/useAgentNames";
import type { Action, CardListItem, Lane, RecentDecision } from "./api";
import { countByFiler, oldestWaitStart, waitStart } from "./lib/board";
import type { SourceGroup } from "./lib/board";
import { dayLabel, describeFiler, formatAge, formatAgo, formatFull, formatWhen } from "./lib/format";
import { FilerTile, Label } from "./parts";

type CardListProps = {
  lane: Lane;
  /** The lane's cards, in the order shown. */
  cards: CardListItem[];
  groups: SourceGroup[];
  now: number;
  selectedId: string | null;
  busyId: string | null;
  /** `byKeyboard` is true when the row was pressed with Enter or Space. */
  onSelect: (id: string, byKeyboard: boolean) => void;
  /** Runs one of a card's actions straight from its row (Undo on a done card). */
  onQuickAction: (card: CardListItem, action: Action) => void;
  /** Snoozed cards, shown under "Needs you" only. */
  snoozed: CardListItem[];
  snoozedOpen: boolean;
  onSnoozedOpen: (open: boolean) => void;
  onUnsnooze: (card: CardListItem) => void;
  recent: RecentDecision[];
  recentOpen: boolean;
  onRecentOpen: (open: boolean) => void;
};

const ROW =
  "flex items-stretch border-b border-l-2 border-b-[color-mix(in_srgb,var(--border)_55%,transparent)] transition-colors";
const ROW_IDLE = "border-l-transparent hover:bg-[color-mix(in_srgb,var(--surface)_60%,transparent)]";
const ROW_SELECTED = "border-l-accent bg-surface";

function Row({
  card,
  selected,
  onSelect,
  trailing,
  children,
}: {
  card: Pick<CardListItem, "id" | "title">;
  selected: boolean;
  onSelect: (id: string, byKeyboard: boolean) => void;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <li className={`${ROW} ${selected ? ROW_SELECTED : ROW_IDLE}`} data-card-id={card.id}>
      <button
        type="button"
        className="block min-w-0 flex-1 py-2 pl-6 pr-3 text-left -outline-offset-2"
        aria-current={selected ? "true" : undefined}
        onClick={(event) => onSelect(card.id, event.detail === 0)}
      >
        {children}
      </button>
      {trailing ? <div className="flex flex-none items-center pr-3">{trailing}</div> : null}
    </li>
  );
}

function CardRow({
  card,
  lane,
  now,
  selected,
  onSelect,
  trailing,
}: {
  card: CardListItem;
  lane: Lane;
  now: number;
  selected: boolean;
  onSelect: (id: string, byKeyboard: boolean) => void;
  trailing?: ReactNode;
}) {
  const filer = describeFiler(card.filed_by, useAgentNames(), card.source);
  // "Needs you" and "Waiting" count how long the card has waited; "Done for you" says when it was done.
  const since = lane === "done" ? card.filed : waitStart(card);
  return (
    <Row card={card} selected={selected} onSelect={onSelect} trailing={trailing}>
      <div className="flex items-center gap-1.5 text-xs text-text-muted">
        <FilerTile filedBy={card.filed_by} source={card.source} />
        <b className="min-w-0 flex-shrink truncate font-semibold text-text">{filer.name}</b>
        <span className="min-w-0 flex-1 truncate" title={card.kind}>
          · {plainKind(card.kind)}
        </span>
        <span className="flex flex-none items-center gap-1 font-mono text-[11px] text-text-faint">
          {card.files > 0 ? (
            <>
              <Paperclip size={12} aria-hidden="true" />
              <span className="sr-only">
                {card.files} {card.files === 1 ? "file" : "files"},
              </span>
            </>
          ) : null}
          {card.snoozed && card.snoozed_until !== null ? (
            <span className="flex items-center gap-1" title={`Snoozed until ${formatFull(card.snoozed_until)}`}>
              <Clock size={12} aria-hidden="true" />
              <span className="sr-only">snoozed until</span>
              {formatWhen(card.snoozed_until, now)}
            </span>
          ) : (
            <span title={formatFull(since)}>
              {formatAge(now - since)}
              {lane === "done" ? <span className="sr-only"> ago</span> : <span className="sr-only"> waiting</span>}
            </span>
          )}
        </span>
      </div>
      <div className={`mt-px truncate text-[13px] ${selected ? "text-text" : "text-text-muted"}`}>
        {card.title || card.summary}
      </div>
    </Row>
  );
}

function Disclosure({
  label,
  count,
  open,
  onOpen,
  children,
}: {
  label: string;
  count: number;
  open: boolean;
  onOpen: (open: boolean) => void;
  children: ReactNode;
}) {
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="border-t border-border">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 px-3.5 py-2 text-left font-mono text-[11px] text-text-muted -outline-offset-2 hover:text-text"
        aria-expanded={open}
        onClick={() => onOpen(!open)}
      >
        <Chevron size={13} aria-hidden="true" />
        {label} ({count})
      </button>
      {open ? children : null}
    </div>
  );
}

/** The left pane: a lane's cards grouped by source and day, flush with hairlines. */
export function CardList({
  lane,
  cards,
  groups,
  now,
  selectedId,
  busyId,
  onSelect,
  onQuickAction,
  snoozed,
  snoozedOpen,
  onSnoozedOpen,
  onUnsnooze,
  recent,
  recentOpen,
  onRecentOpen,
}: CardListProps) {
  const oldest = oldestWaitStart(cards);
  const filers = countByFiler(cards);
  const names = useAgentNames();

  return (
    <>
      <div className="flex flex-wrap gap-x-1.5 gap-y-0.5 border-b border-border px-3 py-2 font-mono text-[11px] text-text-muted">
        <span>
          <b className="font-semibold text-text">{cards.length}</b> in this lane
        </span>
        {filers.map((item) => (
          <span key={item.filer} className="max-w-full truncate">
            · {describeFiler(item.filer, names).name} {item.count}
          </span>
        ))}
        {oldest !== null ? (
          <span title={formatFull(oldest)}>
            · oldest {lane === "done" ? formatAgo(now - oldest) : `waiting ${formatAge(now - oldest)}`}
          </span>
        ) : null}
      </div>

      {cards.length === 0 ? (
        <div className="py-6">
          {lane === "needs" ? (
            <EmptyState icon={Check} title="Nothing needs you" description="Empty means done." compact />
          ) : (
            <EmptyState
              icon={Inbox}
              title="Nothing here"
              description={
                lane === "done"
                  ? "Things done on your behalf will appear here."
                  : "Cards you said not yet to, or snoozed, will appear here."
              }
              compact
            />
          )}
        </div>
      ) : null}

      {groups.map((group) => (
        <section key={group.source} aria-label={`${group.source}, ${group.count} ${group.count === 1 ? "card" : "cards"}`}>
          <h2 className="flex items-center gap-1.5 px-3.5 pb-1 pt-2.5 text-xs font-semibold">
            <span className="truncate">{group.source}</span>
            <span className="font-mono text-[11px] font-normal text-text-faint">{group.count}</span>
          </h2>
          {group.days.map((day) => (
            <div key={day.day}>
              <Label className="pb-[3px] pl-6 pr-3.5 pt-1.5">{dayLabel(day.stamp, now)}</Label>
              <ul>
                {day.cards.map((card) => {
                  const undo = lane === "done" ? card.actions.find((action) => action.id === "undo") : undefined;
                  return (
                    <CardRow
                      key={card.id}
                      card={card}
                      lane={lane}
                      now={now}
                      selected={card.id === selectedId}
                      onSelect={onSelect}
                      trailing={
                        undo ? (
                          <button
                            type="button"
                            className="btn h-6 px-2 text-xs"
                            disabled={busyId === card.id}
                            aria-label={`${undo.label}: ${card.title}`}
                            onClick={() => onQuickAction(card, undo)}
                          >
                            {undo.label}
                          </button>
                        ) : undefined
                      }
                    />
                  );
                })}
              </ul>
            </div>
          ))}
        </section>
      ))}

      {lane === "needs" && snoozed.length > 0 ? (
        <Disclosure label="snoozed" count={snoozed.length} open={snoozedOpen} onOpen={onSnoozedOpen}>
          <ul>
            {snoozed.map((card) => (
              <CardRow
                key={card.id}
                card={card}
                lane="waiting"
                now={now}
                selected={card.id === selectedId}
                onSelect={onSelect}
                trailing={
                  <button
                    type="button"
                    className="btn h-6 px-2 text-xs"
                    disabled={busyId === card.id}
                    aria-label={`Unsnooze: ${card.title}`}
                    onClick={() => onUnsnooze(card)}
                  >
                    Unsnooze
                  </button>
                }
              />
            ))}
          </ul>
        </Disclosure>
      ) : null}

      {recent.length > 0 ? (
        <Disclosure label="recent decisions" count={recent.length} open={recentOpen} onOpen={onRecentOpen}>
          <ul>
            {recent.map((decision) => (
              <Row
                key={`${decision.card_id}-${decision.ts}`}
                card={{ id: decision.card_id, title: decision.title }}
                selected={decision.card_id === selectedId}
                onSelect={onSelect}
              >
                <div className="flex items-center gap-1.5 text-xs text-text-muted">
                  <b className="min-w-0 flex-shrink truncate font-semibold text-text">{decision.label}</b>
                  <span className="min-w-0 flex-1 truncate" title={decision.kind}>
                    · {plainKind(decision.kind)}
                  </span>
                  <span className="flex-none font-mono text-[11px] text-text-faint" title={formatFull(decision.ts)}>
                    {formatAge(now - decision.ts)}
                    <span className="sr-only"> ago</span>
                  </span>
                </div>
                <div className="mt-px truncate text-[13px] text-text-muted">{decision.title}</div>
              </Row>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </>
  );
}
