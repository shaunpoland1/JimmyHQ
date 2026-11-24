import { RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";
import { errorDetail } from "../../api";
import { formatCount } from "../../lib/format";
import type { AgentCard, Candidate, MemoryLine, Snapshot } from "./api";
import { useAgents, useMemory, useNights, useSnapshots } from "./hooks";
import { aboutLabel, describeNightWork, evidenceText, lineCount, newestSnapshots, nightsBySnapshot, pathWords, provenanceOf, sectionLabel, sectionOrder, SECTION_HELP, snapshotTitle, sortCandidates, sortImpressions, sortLines, strengthOf, weightWord } from "./lib/memory";
import { displayName, toMs } from "./lib/status";
import { Flag, Note, Section, dateTime, plainDay } from "./parts";
import { RollbackDialog } from "./RollbackDialog";

type View = "memory" | "nights";

/** What the agent remembers, where each line came from, and the nights that sorted it. */
export function MemoryTab({ agent }: { agent: AgentCard }) {
  const [view, setView] = useState<View>("memory");
  const counts = agent.counts ?? {};
  return (
    <div className="flex flex-col gap-3.5">
      <div className="village-seg" role="group" aria-label="Memory pages">
        <button type="button" aria-pressed={view === "memory"} onClick={() => setView("memory")}>
          What it remembers
          <span className="n">{counts.memory ?? 0}</span>
        </button>
        <button type="button" aria-pressed={view === "nights"} onClick={() => setView("nights")}>
          Nights and rollback
          <span className="n">{counts.snapshots ?? 0}</span>
        </button>
      </div>
      {view === "memory" ? <MemoryPanel agent={agent} /> : <NightsPanel agent={agent} />}
    </div>
  );
}

function MemoryPanel({ agent }: { agent: AgentCard }) {
  const memory = useMemory(agent.id);
  const everyone = useAgents().data ?? [];
  const name = displayName(agent);
  const sections = memory.data?.sections;
  const order = sectionOrder(sections);
  const total = order.reduce((sum, section) => sum + (sections?.[section]?.length ?? 0), 0);
  const candidates = sortCandidates(memory.data?.candidates);
  const impressions = sortImpressions(memory.data?.impressions);

  if (memory.isPending) return <p className="text-sm text-text-faint">Loading its memory…</p>;
  if (memory.isError && !memory.data) {
    return <Note tone="bad">{errorDetail(memory.error)} Only the owner can read an agent's memory: sign in as the owner, then reload the page.</Note>;
  }

  return (
    <>
      {total === 0 ? (
        <Note>
          {name} remembers nothing yet. During the day it proposes things to remember; each night decides what stays. After its first night the lines appear here, each with where it came from.
        </Note>
      ) : (
        <p className="text-xs text-text-muted">
          {formatCount(total)} {total === 1 ? "line" : "lines"} in seven sections. Every line says where it came from. A line is only as good as that: its own account, not checked, is the weakest, and the web, unconfirmed, is a lead and not a fact.
        </p>
      )}
      <div className="village-memory-grid">
        {order.map((section) => (
          <MemorySectionCard key={section} section={section} lines={sections?.[section] ?? []} />
        ))}
      </div>

      <Section title="Waiting for the next night">
        <p className="mb-3 text-xs text-text-muted">
          What {name} asked to remember since it last slept. Nothing here is memory yet: the night looks at each one, checks what it rests on, and keeps, merges or turns it down.
        </p>
        {candidates.length === 0 ? (
          <p className="text-sm text-text-muted">Nothing is waiting. {name} adds to this list as the day goes on.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {candidates.map((candidate, index) => (
              <CandidateRow key={`${candidate.ts}-${index}`} candidate={candidate} />
            ))}
          </ul>
        )}
      </Section>

      <Section title="Its impressions">
        <p className="mb-3 text-xs text-text-muted">
          Its own take on you and on each coworker it has met. Private to {name}, changed only by real meetings, and it may be wrong.
        </p>
        {impressions.length === 0 ? (
          <p className="text-sm text-text-muted">No impressions yet. They are written at night, after {name} has dealt with someone.</p>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {impressions.map((impression) => (
              <li key={impression.about} className="rounded-lg border border-border bg-bg px-3 py-2">
                <p className="text-xs font-medium text-text">Of {impression.about === "owner" ? "you" : aboutLabel(impression.about, everyone)}</p>
                <p className="village-plain mt-1 text-sm text-text">{impression.text}</p>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}

function MemorySectionCard({ section, lines }: { section: string; lines: MemoryLine[] }) {
  const sorted = sortLines(lines);
  const headingId = `village-mem-${section}`;
  return (
    <section className="village-section" aria-labelledby={headingId}>
      <h2 id={headingId} className="flex items-center justify-between gap-2">
        <span>{sectionLabel(section)}</span>
        <span className="font-mono normal-case tracking-normal text-text-faint">{lineCount(lines.length)}</span>
      </h2>
      <p className="mb-2.5 text-xs text-text-faint">{SECTION_HELP[section] ?? ""}</p>
      {sorted.length === 0 ? (
        <p className="text-sm text-text-muted">Nothing here yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {sorted.map((line) => (
            <MemoryLineRow key={line.id} line={line} />
          ))}
        </ul>
      )}
    </section>
  );
}

function MemoryLineRow({ line }: { line: MemoryLine }) {
  const source = provenanceOf(line.source);
  const strength = strengthOf(line.strength);
  const weight = weightWord(line.weight);
  const evidence = line.evidence ?? [];
  return (
    <li className="village-line" data-trust={source.trust}>
      <p className="village-plain text-sm text-text">{line.text}</p>
      <div className="village-line-meta">
        <span className="village-source" data-trust={source.trust} title={source.help}>
          {source.label}
        </span>
        {line.locked ? <Flag tone="quiet">Settled</Flag> : null}
        {weight ? <span title={weight}>{weight.split(":")[0]}</span> : null}
        {strength ? (
          <span className="village-strength" title={`Strength ${strength.percent} of 100`}>
            <span className="village-track" aria-hidden="true">
              <i style={{ width: `${strength.percent}%` }} />
            </span>
            {strength.word}
            <span className="sr-only">, {strength.percent} of 100</span>
          </span>
        ) : null}
        <span>{evidenceText(evidence)}</span>
        {line.confirmed ? <span>Confirmed {line.confirmed} {line.confirmed === 1 ? "time" : "times"} since</span> : null}
      </div>
      {evidence.length > 0 ? (
        <details className="village-raw">
          <summary>Evidence</summary>
          <p className="font-mono text-[11px] leading-5 text-text-muted [overflow-wrap:anywhere]">{evidence.join(", ")}</p>
          <p className="text-[11px] text-text-faint">Event ids from its log. Filter the Activity tab to find what they refer to.</p>
        </details>
      ) : null}
    </li>
  );
}

function CandidateRow({ candidate }: { candidate: Candidate }) {
  const evidence = candidate.evidence ?? [];
  const at = toMs(candidate.ts);
  return (
    <li className="village-line" data-trust="unknown">
      <p className="village-plain text-sm text-text">{candidate.text}</p>
      <div className="village-line-meta">
        <span>For {candidate.section ? sectionLabel(candidate.section).toLowerCase() : "no section"}</span>
        {at ? <span>Asked {dateTime(at)}</span> : null}
        <span>{evidenceText(evidence)}</span>
      </div>
    </li>
  );
}

function NightsPanel({ agent }: { agent: AgentCard }) {
  const snapshots = useSnapshots(agent.id);
  const nights = useNights(agent.id);
  const [rolling, setRolling] = useState<Snapshot | null>(null);
  const name = displayName(agent);
  const list = useMemo(() => newestSnapshots(snapshots.data?.snapshots), [snapshots.data]);
  const byNight = useMemo(() => nightsBySnapshot(nights.data?.events), [nights.data]);

  if (snapshots.isPending) return <p className="text-sm text-text-faint">Loading its nights…</p>;
  if (snapshots.isError && !snapshots.data) {
    return <Note tone="bad">{errorDetail(snapshots.error)} Only the owner can read an agent's snapshots: sign in as the owner, then reload the page.</Note>;
  }

  return (
    <Section title="Nights">
      <p className="mb-3 text-xs text-text-muted">
        After every night and every nap, {name}'s memory is saved as a snapshot, so a bad night can be undone. Rolling back puts memory and impressions back as they were then; the diary only if you ask. The newest are listed first, and only the most recent are kept.
      </p>
      {agent.last_restore ? (
        <div className="mb-3">
          <Note>
            Last rolled back to snapshot {agent.last_restore.to} on {dateTime(toMs(agent.last_restore.ts) ?? 0)}. The memory it had just before is snapshot {agent.last_restore.undo}.
          </Note>
        </div>
      ) : null}
      {nights.isError ? (
        <div className="mb-3">
          <Note tone="warn">What each night did could not be read ({errorDetail(nights.error)}). The snapshots below still work.</Note>
        </div>
      ) : null}
      {list.length === 0 ? (
        <p className="text-sm text-text-muted">
          No snapshots yet. The first is taken after {name}'s first night, once it has slept and sorted its day into memory.
        </p>
      ) : (
        <ol className="flex flex-col gap-2.5">
          {list.map((snapshot) => {
            const work = describeNightWork(byNight.get(snapshot.seq), agent.last_night?.snapshot === snapshot.seq ? agent.last_night : undefined);
            const title = snapshotTitle(snapshot, plainDay);
            const at = toMs(snapshot.ts) ?? 0;
            const isNight = snapshot.reason === "night" || snapshot.reason === "nap";
            return (
              <li key={snapshot.seq} className="village-night">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <h3 className="text-sm font-semibold text-text">{title}</h3>
                  <span className="font-mono text-[11px] text-text-faint">snapshot {snapshot.seq}</span>
                  {agent.last_restore?.to === snapshot.seq ? <Flag tone="quiet">Last rolled back to</Flag> : null}
                  {agent.last_restore?.undo === snapshot.seq ? <Flag tone="quiet">Taken before that rollback</Flag> : null}
                </div>
                <p className="text-xs text-text-faint">
                  {at ? dateTime(at) : "Time not recorded"} · holds {pathWords(snapshot.paths)}
                  {typeof snapshot.files === "number" ? ` (${snapshot.files} ${snapshot.files === 1 ? "file" : "files"})` : ""}
                </p>
                {work ? (
                  <div className="mt-1.5 flex flex-col gap-1 text-sm">
                    <p className={work.failed ? "text-text" : "text-text-muted"}>{work.summary}</p>
                    {work.by ? <p className="text-xs text-text-faint">{work.by}.{work.dreamt ? " It dreamt." : ""}</p> : null}
                    {work.notes ? <p className="village-plain text-text [overflow-wrap:anywhere]">{work.notes}</p> : null}
                  </div>
                ) : isNight ? (
                  <p className="mt-1.5 text-xs text-text-faint">What this night did could not be read from the log.</p>
                ) : null}
                <div className="mt-2.5">
                  <button type="button" className="btn h-7 px-2.5 text-xs" disabled={agent.retired} onClick={() => setRolling(snapshot)}>
                    <RotateCcw size={13} aria-hidden="true" />
                    {isNight ? "Roll back to this night" : "Roll back to this snapshot"}
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {agent.retired ? <p className="mt-3 text-xs text-text-faint">{name} has retired, so its memory can no longer be rolled back. The snapshots stay here to read.</p> : null}
      {rolling ? <RollbackDialog agent={agent} snapshot={rolling} title={snapshotTitle(rolling, plainDay)} at={toMs(rolling.ts) ?? 0} onClose={() => setRolling(null)} /> : null}
    </Section>
  );
}
