import { MessageCircle } from "lucide-react";
import { formatHash } from "../../lib/route";
import type { AgentSummary } from "./api";
import { useProvidersQuery, useRolesQuery } from "../models/queries";
import { useAgent } from "./hooks";
import { agentModelProblem } from "./lib/modelProblem";
import { computeMood, describeMood } from "./lib/needs";
import { describeDoing, displayName, roleLine, waitingOnYou } from "./lib/status";
import type { Colour } from "./lib/status";
import { Flag, Pulse, Sigil, Vitals, colourClass, untilSentence } from "./parts";

type Props = { agent: AgentSummary; colour: Colour; showVitals: boolean; now: number };

/** One agent on the roster: who it is, what it is doing, how it is, and whether it is waiting on the owner. */
export function AgentCard({ agent, colour, showVitals, now }: Props) {
  // The roster row has no mood or wake time; the agent's own card does. A retired agent's never changes.
  const card = useAgent(agent.id, !agent.retired).data;
  const seen = { ...agent, vitals: card?.vitals };
  const doing = describeDoing(seen);
  const name = displayName(agent);
  const needs = agent.needs ?? card?.needs;
  const mood = describeMood(card?.mood ?? (needs && card?.temperament?.mood_weights ? computeMood(needs, card.temperament.mood_weights) : null));
  const roles = useRolesQuery();
  const providers = useProvidersQuery();
  const flags = waitingOnYou(agent);
  const noModel = agent.retired ? null : agentModelProblem(card?.config, roles.data?.roles, providers.data?.providers);
  const until = untilSentence(doing, now);
  const started = doing.state !== "new" && doing.state !== "retired";

  return (
    <article className={`village-card ${colourClass(colour)}`} data-state={doing.state} aria-label={`${name}: ${doing.headline}`}>
      <header className="flex items-center gap-2.5">
        <Sigil agent={agent} />
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-text">
            <a className="village-card-link" href={formatHash({ screen: "village", sub: agent.id })}>
              {name}
            </a>
          </h3>
          <p className="truncate text-xs text-text-muted">{roleLine(agent)}</p>
        </div>
        {started && mood ? <span className="max-w-[45%] flex-none text-right text-xs text-text-muted">{mood}</span> : null}
      </header>

      <div className="village-now">
        <span className="flex items-baseline gap-2">
          <span className="translate-y-[-1px]">
            <Pulse state={doing.state} />
          </span>
          <b>{doing.headline}</b>
        </span>
        {until ?? doing.detail ? <span className="mt-0.5 block pl-[0.9375rem] text-xs">{until ?? doing.detail}</span> : null}
      </div>

      {showVitals && started ? (
        <div className="mt-3">
          <Vitals needs={needs} />
        </div>
      ) : null}

      {noModel ? (
        <p className="village-note mt-3" data-tone="warn">
          {noModel}
        </p>
      ) : null}

      {flags.length > 0 || !agent.retired ? (
        <footer className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-border pt-2.5">
          {flags.map((flag) => (
            <Flag key={flag}>{flag}</Flag>
          ))}
          {agent.retired ? null : (
            <a
              className="village-above btn ml-auto h-7 px-2.5 text-xs"
              href={formatHash({ screen: "village", sub: `${agent.id}/chat` })}
              aria-label={`Chat with ${name}`}
            >
              <MessageCircle size={13} aria-hidden="true" />
              Chat
            </a>
          )}
        </footer>
      ) : null}
    </article>
  );
}
