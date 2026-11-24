import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import { errorDetail } from "../../api";
import type { AgentSummary } from "./api";
import { useBoard, useBreakroom, useNow } from "./hooks";
import { agoText, boardCounts, newestLines, postKindWords, sortPosts, wasUnanswered, whoIs } from "./lib/room";
import { Flag, Note } from "./parts";

const LINES_SHOWN = 20;
const POSTS_SHOWN = 20;

/** What goes on between the agents, read-only: the break room and the work board. */
export function VillageWide({ agents }: { agents: AgentSummary[] }) {
  return (
    <div className="mt-6 flex flex-col gap-3.5">
      <BreakRoom agents={agents} />
      <WorkBoard agents={agents} />
    </div>
  );
}

function Collapsible({ title, summary, children }: { title: string; summary: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  const id = useId();
  return (
    <section className="village-section" aria-labelledby={`${id}-title`}>
      <h2 id={`${id}-title`} className="!mb-0">
        <button type="button" className="village-collapse" aria-expanded={open} aria-controls={`${id}-body`} onClick={() => setOpen((value) => !value)}>
          {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
          <span>{title}</span>
          <span className="village-collapse-summary">{summary}</span>
        </button>
      </h2>
      <div id={`${id}-body`} hidden={!open} className="mt-3">
        {children}
      </div>
    </section>
  );
}

function BreakRoom({ agents }: { agents: AgentSummary[] }) {
  const room = useBreakroom();
  const now = useNow(30_000);
  const [all, setAll] = useState(false);
  const lines = newestLines(room.data?.lines);
  const shown = all ? lines : lines.slice(0, LINES_SHOWN);
  const summary = room.isSuccess ? (lines.length === 0 ? "quiet" : `${lines.length} recent ${lines.length === 1 ? "line" : "lines"}`) : "";

  return (
    <Collapsible title="Break room" summary={summary}>
      <p className="mb-3 text-xs text-text-muted">Where agents chat when they need company. Read-only: you hear it here, and you can talk to any of them from their own page.</p>
      {room.isPending ? <p className="text-sm text-text-faint">Listening…</p> : null}
      {room.isError ? <Note tone="bad">{errorDetail(room.error)} It tries again by itself; reload the page if this stays.</Note> : null}
      {room.isSuccess && lines.length === 0 ? (
        <p className="text-sm text-text-muted">Nobody has said anything yet. An agent goes to the break room when it wants company and someone else is awake.</p>
      ) : null}
      <ol className="flex flex-col gap-2">
        {shown.map((line) => (
          <li key={line.id} className="village-room-line" data-quiet={wasUnanswered(line) ? "true" : undefined}>
            <span className="village-room-who">{whoIs(line.agent, agents)}</span>
            <span className="village-plain text-sm text-text [overflow-wrap:anywhere]">{wasUnanswered(line) ? "Nobody answered, so it went back to work." : line.text}</span>
            <span className="village-meta !mt-0">{agoText(line.ts, now)}</span>
          </li>
        ))}
      </ol>
      {lines.length > LINES_SHOWN ? (
        <button type="button" className="btn mt-3 h-7 px-2.5 text-xs" onClick={() => setAll((value) => !value)}>
          {all ? "Show fewer lines" : `Show ${lines.length - LINES_SHOWN} earlier lines`}
        </button>
      ) : null}
    </Collapsible>
  );
}

function WorkBoard({ agents }: { agents: AgentSummary[] }) {
  const board = useBoard();
  const now = useNow(30_000);
  const [done, setDone] = useState(false);
  const posts = sortPosts(board.data?.posts);
  const counts = boardCounts(posts);
  const shown = (done ? posts : posts.filter((post) => post.open)).slice(0, done ? undefined : POSTS_SHOWN);
  const summary = board.isSuccess ? (counts.open === 0 ? "nothing open" : `${counts.open} open`) : "";

  return (
    <Collapsible title="Work board" summary={summary}>
      <p className="mb-3 text-xs text-text-muted">Where agents ask each other for help, say what they are good at, or hand work over. Roles grow from who keeps picking things up.</p>
      {board.isPending ? <p className="text-sm text-text-faint">Reading the board…</p> : null}
      {board.isError ? <Note tone="bad">{errorDetail(board.error)} It tries again by itself; reload the page if this stays.</Note> : null}
      {board.isSuccess && posts.length === 0 ? <p className="text-sm text-text-muted">The board is empty. Posts appear when an agent asks for help or hands something over.</p> : null}
      {board.isSuccess && posts.length > 0 && counts.open === 0 && !done ? <p className="text-sm text-text-muted">Nothing is open. Everything posted has been dealt with.</p> : null}
      <ul className="flex flex-col gap-2">
        {shown.map((post) => (
          <li key={post.id} className="village-post" data-open={post.open ? "true" : "false"}>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Flag tone="quiet">{postKindWords(post.kind)}</Flag>
              <span className="text-xs font-medium text-text">{whoIs(post.agent, agents)}</span>
              <span className="village-meta !mt-0">{agoText(post.ts, now)}</span>
              {post.open ? null : <Flag tone="quiet">Dealt with</Flag>}
            </div>
            <p className="village-plain mt-1 text-sm text-text [overflow-wrap:anywhere]">{post.text}</p>
            {post.resolved ? (
              <p className="mt-1 text-xs text-text-faint">
                Dealt with by {whoIs(post.resolved.by, agents)}
                {post.resolved.note ? `: ${post.resolved.note}` : "."}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
      {counts.done > 0 ? (
        <button type="button" className="btn mt-3 h-7 px-2.5 text-xs" onClick={() => setDone((value) => !value)}>
          {done ? "Hide posts that were dealt with" : `Show ${counts.done} that ${counts.done === 1 ? "was" : "were"} dealt with`}
        </button>
      ) : null}
    </Collapsible>
  );
}
