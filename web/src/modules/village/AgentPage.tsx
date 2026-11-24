import { ArrowLeft } from "lucide-react";
import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { ApiError, errorDetail } from "../../api";
import { formatHash } from "../../lib/route";
import type { AgentCard, AgentSummary } from "./api";
import { ActivityTab } from "./ActivityTab";
import { BrowserTab } from "./BrowserTab";
import { ChatTab } from "./ChatTab";
import { ConfigSection } from "./ConfigSection";
import { RetireDialog } from "./dialogs";
import { DiaryTab } from "./DiaryTab";
import { FilesTab } from "./FilesTab";
import { HandsTab } from "./HandsTab";
import { MemoryTab } from "./MemoryTab";
import { useAgent, useNow, usePriorities } from "./hooks";
import { computeMood, describeMood, mostStrained, readNeeds } from "./lib/needs";
import { forAgent } from "./lib/priorities";
import { TABS, TAB_LABELS, tabAfterKey } from "./lib/route";
import type { Tab } from "./lib/route";
import { agentLabel } from "./lib/names";
import { colourOf, describeDoing, displayName, waitingOnYou } from "./lib/status";
import { describeNight, describeTemperament } from "./lib/temperament";
import { Flag, Note, Pulse, Section, Sigil, Vitals, colourClass, dateTime, plainDay, untilSentence } from "./parts";

type Props = {
  id: string;
  tab: Tab;
  everyone: AgentSummary[];
  rosterLoaded: boolean;
  showVitals: boolean;
  onTab: (tab: Tab) => void;
  onBack: () => void;
};

/**
 * One agent: the roster card opened up. Each tab gets the full agent card.
 */
export function AgentPage({ id, tab, everyone, rosterLoaded, showVitals, onTab, onBack }: Props) {
  const card = useAgent(id);
  const now = useNow();
  const tabs = useRef<HTMLDivElement>(null);
  const [retiring, setRetiring] = useState(false);
  const back = (
    <a href={formatHash({ screen: "village", sub: null })} className="inline-flex items-center gap-1 text-xs text-text-muted hover:text-text">
      <ArrowLeft size={13} aria-hidden="true" />
      Village
    </a>
  );

  if (!card.data) {
    const missing = card.error instanceof ApiError && card.error.status === 404;
    return (
      <div className="max-w-[1120px] px-3.5 pb-10 pt-4 @2xl:px-[26px]">
        {back}
        <div className="mt-4">
          {card.isPending ? <p className="text-sm text-text-faint">Loading…</p> : null}
          {card.isError ? (
            <Note tone="bad">{missing ? "There is no agent with that id. It may have been typed wrong. Go back to the Village to pick one." : `${errorDetail(card.error)} Reload the page to try again.`}</Note>
          ) : null}
        </div>
      </div>
    );
  }

  const agent = card.data;
  const doing = describeDoing(agent);
  const name = displayName(agent);
  const label = agentLabel(agent);
  const mood = describeMood(agent.mood ?? (agent.needs && agent.temperament?.mood_weights ? computeMood(agent.needs, agent.temperament.mood_weights) : null));
  const flags = waitingOnYou(agent);
  const until = untilSentence(doing, now);
  const colour = colourOf(id, rosterLoaded ? everyone : [{ id }]);
  const unanswered = agent.unanswered ?? 0;

  const onTabKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const next = tabAfterKey(event.key, tab);
    if (!next) return;
    event.preventDefault();
    onTab(next);
    requestAnimationFrame(() => tabs.current?.querySelector<HTMLElement>(`[data-tab="${next}"]`)?.focus());
  };

  const panel =
    tab === "overview" ? (
      <Overview agent={agent} showVitals={showVitals} mood={mood} until={until} onRetire={() => setRetiring(true)} />
    ) : tab === "chat" ? (
      <ChatTab agent={agent} />
    ) : tab === "diary" ? (
      <DiaryTab agent={agent} />
    ) : tab === "memory" ? (
      <MemoryTab agent={agent} />
    ) : tab === "files" ? (
      <FilesTab agent={agent} />
    ) : tab === "integrations" ? (
      <HandsTab agent={agent} />
    ) : tab === "browser" ? (
      <BrowserTab agent={agent} />
    ) : (
      <ActivityTab agent={agent} />
    );

  return (
    <div className={`village-page ${colourClass(colour)} max-w-[1120px] px-3.5 pb-10 pt-4 @2xl:px-[26px]`}>
      {back}
      <header className="village-hero mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-3">
        <Sigil agent={agent} size="lg" />
        <div className="min-w-0 flex-1 basis-56">
          <h1 className="text-xl font-semibold text-text [overflow-wrap:anywhere]">{name}</h1>
          <p className="text-sm text-text-muted">
            {label.role ?? "No role yet"}
            {label.note ? <span className="text-text-faint"> · {label.note}</span> : null}
          </p>
        </div>
        <div className="min-w-0 basis-64 text-sm">
          <p className="flex items-center gap-2 font-medium text-text">
            <Pulse state={doing.state} />
            <span className="[overflow-wrap:anywhere]">{doing.headline}</span>
          </p>
          {until ?? doing.detail ? <p className="pl-[0.9375rem] text-xs text-text-muted">{until ?? doing.detail}</p> : null}
          {mood && !agent.retired ? <p className="pl-[0.9375rem] text-xs text-text-muted">Mood: {mood.toLowerCase()}</p> : null}
        </div>
        {flags.length > 0 ? (
          <div className="flex basis-full flex-wrap gap-1.5">
            {flags.map((flag) => (
              <Flag key={flag}>{flag}</Flag>
            ))}
          </div>
        ) : null}
      </header>

      <div ref={tabs} className="village-tabs mt-4" role="tablist" aria-label={`${name}'s pages`} onKeyDown={onTabKey}>
        {TABS.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            id={`village-tab-${item}`}
            data-tab={item}
            aria-selected={tab === item}
            aria-controls="village-panel"
            tabIndex={tab === item ? 0 : -1}
            onClick={() => onTab(item)}
          >
            {TAB_LABELS[item]}
            {item === "chat" && unanswered > 0 && tab !== "chat" ? (
              <span className="village-badge" aria-label={`${unanswered} unanswered`}>
                {unanswered}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div id="village-panel" role="tabpanel" aria-labelledby={`village-tab-${tab}`} className="mt-4">
        {panel}
      </div>
      {retiring ? <RetireDialog agent={agent} onClose={() => setRetiring(false)} onRetired={onBack} /> : null}
    </div>
  );
}

type OverviewProps = { agent: AgentCard; showVitals: boolean; mood: string | null; until: string | null; onRetire: () => void };

function Overview({ agent, showVitals, mood, until, onRetire }: OverviewProps) {
  const priorities = usePriorities();
  const doing = describeDoing(agent);
  const name = displayName(agent);
  const parked = agent.vitals?.parked ?? [];
  const waiting = agent.waiting ?? 0;
  const strained = mostStrained(readNeeds(agent.needs));
  const temperament = describeTemperament(agent.temperament);
  const night = describeNight(agent.last_night);
  const mine = forAgent(priorities.data ?? [], agent.id);
  const counts = agent.counts ?? {};
  const logged = Object.values(counts.events_today ?? {}).reduce((sum, value) => sum + value, 0);

  return (
    <div className="village-sections">
      <div className="village-stack">
        <Section title="Right now">
          <p className="text-sm text-text">
            <b className="font-semibold">{doing.headline}</b>
            {until ?? doing.detail ? <span className="text-text-muted"> · {until ?? doing.detail}</span> : null}
          </p>
          {agent.retired ? null : (
            <dl className="mt-3 grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
              <dt className="text-text-muted">Current task</dt>
              <dd className="[overflow-wrap:anywhere]">{agent.current_task || <span className="text-text-faint">None in hand</span>}</dd>
              <dt className="text-text-muted">Set aside</dt>
              <dd className="[overflow-wrap:anywhere]">
                {parked.length === 0 ? (
                  <span className="text-text-faint">Nothing</span>
                ) : (
                  <ul className="list-disc pl-4">
                    {parked.map((task) => (
                      <li key={task}>{task}</li>
                    ))}
                  </ul>
                )}
              </dd>
              <dt className="text-text-muted">Waiting on you</dt>
              <dd>{waiting === 0 ? <span className="text-text-faint">Nothing</span> : `${waiting} ${waiting === 1 ? "thing" : "things"}. Look for its cards on the Bench.`}</dd>
            </dl>
          )}
          {parked.length > 0 ? (
            <p className="mt-2 text-xs text-text-faint">A task is set aside when frustration runs high. It comes back by itself once {name} is calm.</p>
          ) : null}
        </Section>

        {showVitals && !agent.retired ? (
          <Section title="Vitals">
            {mood ? <p className="mb-3 text-sm text-text">{mood}.</p> : null}
            <Vitals needs={agent.needs} detail="full" />
            {strained ? (
              <p className="mt-3 text-xs text-text-muted">
                {strained.label} is the one to watch: {strained.word}
                {strained.highIsBad ? " (for this need, lower is better)" : ""}.
              </p>
            ) : null}
            <p className="mt-2 text-xs text-text-faint">
              Each need runs from 0 to 100 and is read again every few seconds. Energy, company and purpose are good when full. Frustration and curiosity are urges: a full bar
              means the urge is strong. Its log of every change is on the Activity tab.
            </p>
          </Section>
        ) : null}

        <Section title="Last night">
          {night && agent.last_night ? (
            <div className="flex flex-col gap-1.5 text-sm">
              <p className="text-xs text-text-faint">
                The night after {plainDay(agent.last_night.day)}, finished {dateTime(agent.last_night.ts)}
              </p>
              {agent.last_night.notes ? <p className="text-text [overflow-wrap:anywhere]">{agent.last_night.notes}</p> : null}
              <p className="text-text-muted">
                {night.summary} {night.by}
              </p>
              {night.rejected ? <p className="text-text-muted">{night.rejected}</p> : null}
              {typeof agent.last_night.snapshot === "number" ? (
                <p className="text-xs text-text-faint">Its memory as it was before is kept as snapshot {agent.last_night.snapshot}.</p>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-text-muted">No night yet. After its first sleep, this says what it sorted into memory, which model did the sorting, and how much was rejected.</p>
          )}
        </Section>

        <Section title="Priorities it sees">
          {mine.length === 0 ? (
            <p className="text-sm text-text-muted">None are active for {name}. Add one from the Village page.</p>
          ) : (
            <ul className="flex flex-col gap-1.5 text-sm">
              {mine.map((priority) => (
                <li key={priority.id} className="[overflow-wrap:anywhere]">
                  <span className="font-medium text-text">{priority.title}</span>
                  {priority.why ? <span className="text-text-muted"> · {priority.why}</span> : null}
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      <div className="village-stack">
        <Section title="Temperament">
          {temperament.length === 0 ? (
            <p className="text-sm text-text-muted">Not rolled yet. It is rolled once, at birth, and never changes.</p>
          ) : (
            <>
              <ul className="flex flex-col gap-1 text-sm text-text">
                {temperament.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-text-faint">Rolled once at birth{typeof agent.seed === "number" ? ` from seed ${agent.seed}` : ""}. It cannot be edited.</p>
            </>
          )}
        </Section>

        <ConfigSection agent={agent} />

        <Section title="About">
          <dl className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
            <dt className="text-text-muted">Id</dt>
            <dd className="font-mono text-xs leading-5">{agent.id}</dd>
            <dt className="text-text-muted">Born</dt>
            <dd>{agent.born_ts ? dateTime(agent.born_ts) : <span className="text-text-faint">Not recorded</span>}</dd>
            {agent.retired_ts ? (
              <>
                <dt className="text-text-muted">Retired</dt>
                <dd>{dateTime(agent.retired_ts)}</dd>
              </>
            ) : null}
            <dt className="text-text-muted">Remembers</dt>
            <dd>
              {counts.memory ?? 0} {counts.memory === 1 ? "thing" : "things"}
              {counts.candidates ? `, ${counts.candidates} more waiting for a night` : ""}
            </dd>
            <dt className="text-text-muted">Diary</dt>
            <dd>
              {counts.diary_days ?? 0} {counts.diary_days === 1 ? "day" : "days"}
            </dd>
            <dt className="text-text-muted">Last 24 hours</dt>
            <dd>
              {logged} logged {logged === 1 ? "event" : "events"}
            </dd>
          </dl>
          {agent.last_restore ? (
            <p className="mt-2 text-xs text-text-muted">
              Its memory was rolled back to snapshot {agent.last_restore.to} on {dateTime(agent.last_restore.ts)}
              {agent.last_restore.note ? `: ${agent.last_restore.note}` : "."}
            </p>
          ) : null}
        </Section>

        {agent.retired ? null : (
          <Section title="Retire">
            <p className="text-sm text-text-muted">Retiring stops {name} for good. Its folder is archived and what it shared stays readable; nothing is deleted.</p>
            <button type="button" className="btn mt-3 border-danger text-danger" onClick={onRetire}>
              Retire {name}…
            </button>
          </Section>
        )}
      </div>
    </div>
  );
}
