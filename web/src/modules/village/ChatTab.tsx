import { useQueryClient } from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";
import { ArrowDown, Send } from "lucide-react";
import { useId, useLayoutEffect, useRef, useState } from "react";
import { errorDetail } from "../../api";
import { villageApi } from "./api";
import type { AgentSummary, ChatPage } from "./api";
import { AGENTS_KEY, agentKey, chatKey, useChat } from "./hooks";
import { deliveryNote, groupByDay, lastSeq, messageProblem, unseenCount, unseenLabel } from "./lib/chat";
import { displayName } from "./lib/status";
import { Note, clockTime, dayLabel } from "./parts";

const NEAR_BOTTOM_PX = 48;

/** The owner's conversation with one agent. */
export function ChatTab({ agent }: { agent: AgentSummary }) {
  const client = useQueryClient();
  const { query, messages, asleep } = useChat(agent.id);
  const name = displayName(agent);
  const scroller = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const shape = useRef({ first: 0, last: 0, height: 0 });
  const [seenSeq, setSeenSeq] = useState(0);
  const [text, setText] = useState("");
  const [urgent, setUrgent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [sentNote, setSentNote] = useState<string | null>(null);
  const id = useId();

  const first = messages[0]?.seq ?? 0;
  const last = lastSeq(messages);
  const lastSender = messages[messages.length - 1]?.sender;

  const toBottom = () => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
    atBottom.current = true;
    setSeenSeq(last);
  };

  // Stay at the newest line while the owner is reading it; hold still when they have
  // scrolled up, and keep their place when earlier lines are added above.
  useLayoutEffect(() => {
    const node = scroller.current;
    if (!node) return;
    const before = shape.current;
    if (before.first !== 0 && first < before.first) node.scrollTop += node.scrollHeight - before.height;
    else if (last !== before.last && (atBottom.current || before.last === 0 || lastSender === "owner")) {
      node.scrollTop = node.scrollHeight;
      atBottom.current = true;
      setSeenSeq(last);
    }
    shape.current = { first, last, height: node.scrollHeight };
  }, [first, last, lastSender]);

  const onScroll = () => {
    const node = scroller.current;
    if (!node) return;
    atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < NEAR_BOTTOM_PX;
    if (atBottom.current && seenSeq !== last) setSeenSeq(last);
  };

  const send = async () => {
    const trouble = messageProblem(text);
    if (trouble) {
      setProblem(trouble);
      return;
    }
    if (busy) return;
    setBusy(true);
    setProblem(null);
    setSentNote(null);
    try {
      const sent = await villageApi.say(agent.id, text.trim(), urgent);
      client.setQueryData<InfiniteData<ChatPage, number | undefined>>(chatKey(agent.id), (data) => {
        const [newest, ...older] = data?.pages ?? [];
        if (!data || !newest) return data;
        return { ...data, pages: [{ ...newest, messages: [...newest.messages.filter((message) => message.seq !== sent.seq), sent] }, ...older] };
      });
      setText("");
      setUrgent(false);
      setSentNote(deliveryNote(sent));
      void client.invalidateQueries({ queryKey: AGENTS_KEY });
      void client.invalidateQueries({ queryKey: agentKey(agent.id) });
    } catch (error) {
      setProblem(`${errorDetail(error)} Your message is still in the box.`);
    } finally {
      setBusy(false);
    }
  };

  const unseen = unseenCount(messages, seenSeq);
  const groups = groupByDay(messages, (ts) => dayLabel(ts));

  return (
    <div className="village-chat">
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div ref={scroller} className="village-chat-scroll" onScroll={onScroll} role="log" aria-label={`Conversation with ${name}`} tabIndex={0}>
          {query.hasNextPage ? (
            <button type="button" className="btn mx-auto h-7 flex-none px-2.5 text-xs" onClick={() => void query.fetchNextPage()} disabled={query.isFetchingNextPage}>
              {query.isFetchingNextPage ? "Loading…" : "Load earlier messages"}
            </button>
          ) : null}
          {query.isPending ? <p className="m-auto text-sm text-text-faint">Loading the conversation…</p> : null}
          {query.isError && messages.length === 0 ? <Note tone="bad">{errorDetail(query.error)} Only the owner can read an agent's chat: sign in as the owner, then reload.</Note> : null}
          {query.isSuccess && messages.length === 0 ? (
            <p className="m-auto max-w-sm text-center text-sm text-text-muted">
              Nothing said yet. Write to {name} below; it reads your message at its next turn and answers here when it has something to say.
            </p>
          ) : null}
          {groups.map((group) => (
            <div key={`${group.day}-${group.messages[0]?.seq}`} className="flex flex-col gap-2">
              <span className="village-day">{group.day}</span>
              {group.messages.map((message) => (
                <div key={message.seq} className="village-bubble" data-sender={message.sender} data-urgent={message.urgent ? "true" : undefined}>
                  <span className="sr-only">{message.sender === "owner" ? "You: " : `${name}: `}</span>
                  {message.text}
                  <span className="village-meta">
                    {clockTime(message.ts)}
                    {message.urgent ? " · urgent" : ""}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
        {unseen > 0 ? (
          <button type="button" className="btn btn-primary absolute bottom-3 left-1/2 h-7 -translate-x-1/2 px-3 text-xs shadow-pop" onClick={toBottom}>
            <ArrowDown size={13} aria-hidden="true" />
            {unseenLabel(unseen)}
          </button>
        ) : null}
      </div>

      {agent.retired ? (
        <div className="border-t border-border p-3">
          <Note>{name} has retired, so it can no longer be written to. The conversation stays here to read.</Note>
        </div>
      ) : (
        <form
          className="flex flex-col gap-2 border-t border-border p-3"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          {asleep ? (
            <Note>{name} is asleep. A message waits until it wakes, unless you mark it urgent.</Note>
          ) : null}
          <div className="flex items-end gap-2">
            <label htmlFor={`${id}-text`} className="sr-only">
              Message to {name}
            </label>
            <textarea
              id={`${id}-text`}
              className="village-textarea"
              rows={2}
              value={text}
              placeholder={`Write to ${name}`}
              aria-invalid={problem ? true : undefined}
              aria-describedby={problem ? `${id}-problem` : undefined}
              onChange={(event) => {
                setText(event.target.value);
                if (problem) setProblem(null);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void send();
                }
              }}
            />
            <button type="submit" className="btn btn-primary" disabled={busy}>
              <Send size={14} aria-hidden="true" />
              Send
            </button>
          </div>
          <label className="flex items-start gap-2 text-xs text-text-muted">
            <input type="checkbox" className="mt-0.5" checked={urgent} onChange={(event) => setUrgent(event.target.checked)} aria-describedby={`${id}-urgent`} />
            <span>
              <span className="font-medium text-text">Urgent: wake them</span>
              <span id={`${id}-urgent`} className="block text-text-faint">
                An urgent message is a call. If {name} is asleep, it is woken to read it straight away. Without this, a message to a sleeping agent waits until it wakes by itself. Keep it for things that cannot wait.
              </span>
            </span>
          </label>
          {problem ? (
            <p id={`${id}-problem`} className="text-xs text-danger" role="alert">
              {problem}
            </p>
          ) : null}
          <p className="text-xs text-text-faint" role="status">
            {sentNote ?? "Enter sends. Shift and Enter starts a new line."}
          </p>
        </form>
      )}
    </div>
  );
}
