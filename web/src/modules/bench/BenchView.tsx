import { Inbox, Keyboard } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { errorDetail } from "../../api";
import { EmptyState } from "../../components/EmptyState";
import { Modal } from "../../components/Modal";
import { Switch } from "../../components/Switch";
import { parseBoolean, usePersistentState } from "../../hooks/usePersistentState";
import { useSettingsQuery } from "../../hooks/useSettings";
import { useToast } from "../../hooks/useToast";
import type { Action, Card, CardListItem, Lane } from "./api";
import { CardList } from "./CardList";
import { LANES, cardToPin, findCard, flatOrder, groupCards, moveSelection, nextAfter, parseSortOrder, shownLane, snoozedCards } from "./lib/board";
import type { SortOrder } from "./lib/board";
import { describeOutcome, formatWhen, reasonNeeded } from "./lib/format";
import { KEYS_OFF_HINT, KEYS_OFF_NOTE, KEY_HELP, KEY_RULES, actionFor, benchCommand, singleKeysOn } from "./lib/keys";
import { EMPTY_DRAFT, ReadingPane } from "./ReadingPane";
import type { Draft } from "./ReadingPane";
import { PickTimeDialog } from "./SnoozeMenu";
import { useBenchActions, useBenchBoard, useBenchCard } from "./useBench";

const CLOCK_TICK_MS = 30_000;

/** The Bench: the owner's single inbox, laid out like a mail client. */
export function BenchView() {
  const board = useBenchBoard();
  const actions = useBenchActions();
  const toast = useToast();
  const settings = useSettingsQuery();

  const [lane, setLane] = useState<Lane>("needs");
  const [sort, setSort] = usePersistentState<SortOrder>("jimmyhq.bench.sort", "oldest", parseSortOrder);
  const [autoAdvance, setAutoAdvance] = usePersistentState("jimmyhq.bench.auto_advance", true, parseBoolean);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Below the two-pane width the list and the card are two steps.
  const [reading, setReading] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [snoozeOpen, setSnoozeOpen] = useState(false);
  const [snoozedOpen, setSnoozedOpen] = useState(false);
  const [recentOpen, setRecentOpen] = useState(false);
  const [keysOpen, setKeysOpen] = useState(false);
  const [pickTimeFor, setPickTimeFor] = useState<CardListItem | Card | null>(null);
  const [focusReason, setFocusReason] = useState(0);
  const [scrollTo, setScrollTo] = useState(0);

  const root = useRef<HTMLDivElement>(null);
  const listPane = useRef<HTMLDivElement>(null);
  const readerPane = useRef<HTMLDivElement>(null);
  const reasonRef = useRef<HTMLInputElement>(null);
  const tabsId = useId();
  const autoLabelId = useId();

  // The clock follows the server's, so ages are right even if this device's clock is not.
  const [tick, setTick] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setTick(Date.now()), CLOCK_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  const data = board.data;
  const now = data ? data.now + Math.max(0, tick - board.dataUpdatedAt) : tick;

  const cards = useMemo(() => (data ? data.lanes[lane] : []), [data, lane]);
  const groups = useMemo(() => groupCards(cards, sort), [cards, sort]);
  const snoozed = useMemo(() => (data ? snoozedCards(data) : []), [data]);
  const order = useMemo(() => {
    const ids = flatOrder(groups);
    return lane === "needs" && snoozedOpen ? [...ids, ...snoozed.map((card) => card.id)] : ids;
  }, [groups, lane, snoozedOpen, snoozed]);

  // With nothing chosen the first card is shown, as in a mail client.
  const currentId = selectedId ?? order[0] ?? null;
  const listItem = data && currentId ? findCard(data, currentId) : undefined;
  const detail = useBenchCard(currentId).data;
  const current: Card | CardListItem | undefined = detail ?? listItem;
  const showReader = reading && currentId !== null;

  const snoozeSetting = settings.data?.settings.find((setting) => setting.key === "bench.snooze_hours")?.value;
  const snoozeHours = typeof snoozeSetting === "number" ? snoozeSetting : undefined;
  const lettersOn = singleKeysOn(settings.data?.settings.find((setting) => setting.key === "bench.single_keys")?.value);

  const draftOf = (id: string | null): Draft => (id ? drafts[id] : undefined) ?? EMPTY_DRAFT;
  const patchDraft = useCallback((id: string, patch: Partial<Draft>) => {
    setDrafts((all) => ({ ...all, [id]: { ...(all[id] ?? EMPTY_DRAFT), ...patch } }));
  }, []);
  const clearDraft = useCallback((id: string) => {
    setDrafts((all) => {
      const { [id]: _gone, ...rest } = all;
      return rest;
    });
  }, []);

  // What the keyboard handler and the async actions read, without re-subscribing.
  const live = useRef({ order, currentId, current, autoAdvance, busyId, snoozeOpen, drafts, lettersOn });
  live.current = { order, currentId, current, autoAdvance, busyId, snoozeOpen, drafts, lettersOn };

  const select = useCallback((id: string, byKeyboard: boolean) => {
    setSelectedId(id);
    setReading(true);
    setSnoozeOpen(false);
    // Below the two-pane width the list is hidden now, so focus has to follow into the card.
    requestAnimationFrame(() => {
      if (byKeyboard || listPane.current?.offsetParent === null) readerPane.current?.focus();
    });
  }, []);

  const backToList = useCallback(() => {
    setReading(false);
    const id = live.current.currentId;
    requestAnimationFrame(() => {
      for (const row of listPane.current?.querySelectorAll<HTMLElement>("[data-card-id]") ?? []) {
        if (row.dataset.cardId === id) {
          row.querySelector("button")?.focus();
          return;
        }
      }
    });
  }, []);

  // Once a card is showing it stays put: a card arriving at the top of the list
  // must not swap the pane under the owner while they read or type.
  const firstId = order[0] ?? null;
  // The card a decision is being sent for is not pinned again: it is leaving the list.
  useEffect(() => {
    const pin = cardToPin(selectedId, firstId, busyId);
    if (pin !== selectedId) setSelectedId(pin);
  }, [selectedId, firstId, busyId]);

  useEffect(() => {
    if (readerPane.current) readerPane.current.scrollTop = 0;
  }, [currentId]);

  const changeLane = useCallback((next: Lane) => {
    setLane(next);
    setSelectedId(null);
    setReading(false);
    setSnoozeOpen(false);
  }, []);

  /** Moves on from a card that is leaving the list; returns how to come back if the server refuses. */
  const advanceFrom = useCallback((id: string) => {
    const state = live.current;
    if (!state.autoAdvance || state.currentId !== id) return () => {};
    // A card opened from elsewhere (a recent decision) is not in the list: stay on it.
    if (!state.order.includes(id)) return () => {};
    const next = nextAfter(state.order, id);
    setSelectedId(next);
    if (next === null) setReading(false);
    return () => {
      if (live.current.currentId === next) setSelectedId(id);
    };
  }, []);

  const act = useCallback(
    async (card: CardListItem | Card, action: Action) => {
      const state = live.current;
      if (state.busyId || card.status !== "open") return;
      const draft = state.drafts[card.id] ?? EMPTY_DRAFT;
      if (action.needs_reason) {
        const asking = draft.reasonFor === action.id;
        if (!asking || !draft.reason.trim()) {
          // Never sent without a reason: show the field and put the cursor in it.
          patchDraft(card.id, { reasonFor: action.id, error: asking ? reasonNeeded(action.label) : null });
          setSelectedId(card.id);
          setReading(true);
          setFocusReason((value) => value + 1);
          return;
        }
      }
      const leaves = action.closes || (action.moves_to !== null && action.moves_to !== shownLane(card));
      setBusyId(card.id);
      const comeBack = leaves ? advanceFrom(card.id) : () => {};
      const result = await actions.decide(card, action, draft.note, action.needs_reason ? draft.reason : "");
      setBusyId(null);
      if (result.ok) {
        clearDraft(card.id);
        toast.success(describeOutcome(action, card.title));
      } else {
        comeBack();
        if (result.status === 422 && action.needs_reason) {
          patchDraft(card.id, { reasonFor: action.id, error: result.detail });
          setFocusReason((value) => value + 1);
        }
      }
    },
    [actions, advanceFrom, clearDraft, patchDraft, toast],
  );

  const snooze = useCallback(
    async (card: CardListItem | Card, until?: number) => {
      if (live.current.busyId) return;
      setBusyId(card.id);
      const comeBack = card.snoozed ? () => {} : advanceFrom(card.id);
      const result = await actions.snooze(card, until);
      setBusyId(null);
      if (result.ok) {
        const back = result.card.snoozed_until;
        toast.success(back === null ? "Snoozed." : `Snoozed until ${formatWhen(back, Date.now())}.`);
      } else comeBack();
    },
    [actions, advanceFrom, toast],
  );

  const unsnooze = useCallback(
    async (card: CardListItem | Card) => {
      if (live.current.busyId) return;
      setBusyId(card.id);
      const result = await actions.unsnooze(card);
      setBusyId(null);
      if (result.ok) toast.success("Unsnoozed: it is back in Needs you.");
    },
    [actions, toast],
  );

  // Keep the chosen row in view after a keyboard move.
  useEffect(() => {
    if (scrollTo === 0) return;
    const id = live.current.currentId;
    if (!id) return;
    for (const row of listPane.current?.querySelectorAll<HTMLElement>("[data-card-id]") ?? []) {
      if (row.dataset.cardId === id) {
        row.scrollIntoView({ block: "nearest" });
        // Focus follows the selection down the list, so Enter opens the chosen card and not the row focus was left on.
        if (listPane.current?.contains(document.activeElement)) row.querySelector("button")?.focus({ preventScroll: true });
        break;
      }
    }
  }, [scrollTo]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      // A dialog (Settings, the command palette, one of ours) owns the keyboard while it is open.
      if (document.querySelector('[role="dialog"]')) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      // Keys pressed in the sidebar, the top strip or the dock are not this screen's:
      // a stray "a" on a control over there must never approve the card shown here.
      const nowhere = target === null || target === document.body || target === document.documentElement;
      const outside = !nowhere && !root.current?.contains(target);
      const command = benchCommand({
        outside,
        // Focus on the page itself (after a click on plain text): the keys move and open, but none decides.
        nowhere,
        lettersOff: !live.current.lettersOn,
        key: event.key,
        metaKey: event.metaKey,
        ctrlKey: event.ctrlKey,
        altKey: event.altKey,
        repeat: event.repeat,
        targetTag: target?.tagName,
        editable: target?.isContentEditable,
        targetRole: target?.getAttribute("role"),
      });
      if (!command) return;
      const state = live.current;
      if (state.snoozeOpen && command !== "snooze") return;

      if (command === "help") {
        event.preventDefault();
        setKeysOpen(true);
        return;
      }
      if (command === "next" || command === "previous") {
        event.preventDefault();
        const next = moveSelection(state.order, state.currentId, command === "next" ? 1 : -1);
        if (next) {
          setSelectedId(next);
          setScrollTo((value) => value + 1);
        }
        return;
      }
      if (command === "open") {
        if (!state.currentId) return;
        event.preventDefault();
        setSelectedId(state.currentId);
        setReading(true);
        // Wait for the pane to be shown before moving focus into it.
        requestAnimationFrame(() => readerPane.current?.focus());
        return;
      }
      // The rest act on the card in the reading pane, so it has to be the one on screen.
      const card = state.current;
      const paneShown = readerPane.current !== null && readerPane.current.offsetParent !== null;
      if (!card || card.status !== "open" || !paneShown) return;
      if (command === "snooze") {
        if (card.lane !== "needs" || state.busyId) return;
        event.preventDefault();
        setSnoozeOpen((open) => !open);
        return;
      }
      const action = actionFor(card.actions, command);
      if (!action) return;
      event.preventDefault();
      void act(card, action);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [act]);

  const onTabKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const index = LANES.findIndex((item) => item.id === lane);
    const target =
      event.key === "ArrowRight" ? index + 1 : event.key === "ArrowLeft" ? index - 1 : event.key === "Home" ? 0 : event.key === "End" ? LANES.length - 1 : null;
    if (target === null) return;
    event.preventDefault();
    const next = LANES[(target + LANES.length) % LANES.length];
    if (!next) return;
    changeLane(next.id);
    document.getElementById(`${tabsId}-${next.id}`)?.focus();
  };

  const header = (
    <header className="flex flex-wrap items-center gap-x-2.5 gap-y-2 border-b border-border px-4 py-3">
      <h1 className="text-[17px] font-semibold">Bench</h1>
      <div
        role="tablist"
        aria-label="Lanes"
        onKeyDown={onTabKey}
        className="inline-flex max-w-full gap-0.5 overflow-x-auto rounded-[7px] border border-border bg-surface p-0.5"
      >
        {LANES.map((item) => {
          const selected = item.id === lane;
          return (
            <button
              key={item.id}
              id={`${tabsId}-${item.id}`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${tabsId}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => changeLane(item.id)}
              className={`flex h-6 items-center gap-1.5 whitespace-nowrap rounded-[5px] px-2.5 text-[12.5px] -outline-offset-1 ${
                selected ? "bg-surface-2 font-semibold text-text shadow-[inset_0_0_0_1px_var(--border)]" : "text-text-muted hover:text-text"
              }`}
            >
              {item.label}
              <span className="font-mono text-[11px] font-normal text-text-faint">{data ? data.counts[item.id] : "–"}</span>
            </button>
          );
        })}
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-2">
        <select
          className="input h-7 w-auto py-0 pl-2 pr-1 text-xs"
          aria-label="Sort order"
          value={sort}
          onChange={(event) => setSort(parseSortOrder(event.target.value) ?? "oldest")}
        >
          <option value="oldest">Oldest first</option>
          <option value="newest">Newest activity first</option>
        </select>
        <span className="flex items-center gap-1.5 text-xs text-text-muted">
          <span id={autoLabelId}>Auto-advance</span>
          <Switch checked={autoAdvance} onChange={setAutoAdvance} labelledBy={autoLabelId} />
        </span>
        <button type="button" className="icon-btn" aria-label="Show keyboard shortcuts" onClick={() => setKeysOpen(true)}>
          <Keyboard size={16} aria-hidden="true" />
        </button>
      </div>
    </header>
  );

  let body;
  if (!data) {
    body = board.isError ? (
      <EmptyState icon={Inbox} title="The Bench is unavailable" description={errorDetail(board.error)} level={2}>
        <button type="button" className="btn" onClick={() => void board.refetch()}>
          Try again
        </button>
      </EmptyState>
    ) : (
      <p className="p-6 text-sm text-text-faint">Loading the Bench…</p>
    );
  } else {
    body = (
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(0,1fr)] @2xl:grid-cols-[minmax(270px,350px)_minmax(0,1fr)]">
        <div
          ref={listPane}
          id={`${tabsId}-panel`}
          role="tabpanel"
          aria-labelledby={`${tabsId}-${lane}`}
          className={`min-h-0 min-w-0 overflow-y-auto @2xl:block @2xl:border-r @2xl:border-border ${showReader ? "hidden" : ""}`}
        >
          {board.isError ? (
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-xs text-danger" role="alert">
              <span className="min-w-0 flex-1">{errorDetail(board.error)}</span>
              <button type="button" className="btn h-6 px-2 text-xs" onClick={() => void board.refetch()}>
                Try again
              </button>
            </div>
          ) : null}
          <CardList
            lane={lane}
            cards={cards}
            groups={groups}
            now={now}
            selectedId={currentId}
            busyId={busyId}
            onSelect={select}
            onQuickAction={(card, action) => void act(card, action)}
            snoozed={snoozed}
            snoozedOpen={snoozedOpen}
            onSnoozedOpen={setSnoozedOpen}
            onUnsnooze={(card) => void unsnooze(card)}
            recent={data.recent}
            recentOpen={recentOpen}
            onRecentOpen={setRecentOpen}
          />
        </div>
        <section
          ref={readerPane}
          tabIndex={-1}
          aria-label={current ? `Card: ${current.title}` : "Card"}
          className={`min-h-0 min-w-0 overflow-y-auto bg-surface outline-none @2xl:block ${showReader ? "" : "hidden"}`}
        >
          <ReadingPane
            cardId={currentId}
            listItem={listItem}
            now={now}
            draft={draftOf(currentId)}
            onDraft={(patch) => {
              if (currentId) patchDraft(currentId, patch);
            }}
            busy={busyId !== null}
            onAct={(card, action) => void act(card, action)}
            onSnooze={(card, until) => void snooze(card, until)}
            onPickTime={setPickTimeFor}
            onUnsnooze={(card) => void unsnooze(card)}
            onBack={backToList}
            snoozeOpen={snoozeOpen}
            onSnoozeOpen={setSnoozeOpen}
            snoozeHours={snoozeHours}
            snoozeChoices={data.snooze_choices}
            timezone={data.timezone}
            reasonRef={reasonRef}
            focusReason={focusReason}
          />
        </section>
      </div>
    );
  }

  return (
    <div ref={root} className="@container flex h-full min-h-0 flex-col">
      {header}
      {body}
      {pickTimeFor ? (
        <PickTimeDialog
          title={pickTimeFor.title}
          now={now}
          choices={data?.snooze_choices}
          onCancel={() => setPickTimeFor(null)}
          onSnooze={(until) => {
            const card = pickTimeFor;
            setPickTimeFor(null);
            void snooze(card, until);
          }}
        />
      ) : null}
      {keysOpen ? (
        <Modal title="Bench keys" onClose={() => setKeysOpen(false)} widthClass="max-w-sm">
          <dl className="flex flex-col gap-2 overflow-y-auto px-4 py-4 text-[13px]">
            {KEY_HELP.map((row) => (
              <div key={row.does} className="flex items-baseline gap-3">
                <dt className="flex w-16 flex-none gap-1">
                  {row.keys.map((key) => (
                    <kbd key={key} className="rounded border border-border bg-bg px-1 font-mono text-[11px] text-text-muted">
                      {key}
                    </kbd>
                  ))}
                </dt>
                <dd className="min-w-0 text-text-muted">{row.does}</dd>
              </div>
            ))}
          </dl>
          <p className="border-t border-border px-4 py-3 text-xs text-text-faint">
            {lettersOn ? `${KEY_RULES} ${KEYS_OFF_HINT}` : KEYS_OFF_NOTE}
          </p>
        </Modal>
      ) : null}
    </div>
  );
}
