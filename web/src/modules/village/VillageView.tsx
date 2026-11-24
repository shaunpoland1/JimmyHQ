import { Plus, Users } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { errorDetail } from "../../api";
import { useHashRoute } from "../../hooks/useHashRoute";
import { useProvidersQuery, useRolesQuery } from "../models/queries";
import { AgentCard } from "./AgentCard";
import { AgentPage } from "./AgentPage";
import type { AgentSummary } from "./api";
import { CreateAgentDialog } from "./dialogs";
import { useAgents, useNow, useVillageFeed, useVillageSettings } from "./hooks";
import { villageModelProblem } from "./lib/modelProblem";
import { parseVillageRoute, villageSub } from "./lib/route";
import type { Tab } from "./lib/route";
import { FILTERS, FILTER_LABELS, colourOf, filterCounts, headcount, matchesFilter, sortRoster } from "./lib/status";
import type { Filter } from "./lib/status";
import { Note } from "./parts";
import { PrioritiesPanel } from "./PrioritiesPanel";
import { VillageWide } from "./VillageWide";

/** The Village: the roster at `#/village`, one agent at `#/village/<id>`. */
export function VillageView() {
  const [route, navigate] = useHashRoute();
  // Settings opens over this screen and takes the route; the Village stays where it was behind it.
  const [seen, setSeen] = useState(route);
  const [sub, setSub] = useState<string | null>(route.screen === "village" ? route.sub : null);
  if (seen !== route) {
    setSeen(route);
    if (route.screen === "village") setSub(route.sub);
  }
  const where = parseVillageRoute(sub);

  useVillageFeed();
  const agents = useAgents();
  const settings = useVillageSettings();
  const [creating, setCreating] = useState(false);

  const everyone = useMemo(() => agents.data ?? [], [agents.data]);
  const living = useMemo(() => everyone.filter((agent) => !agent.retired), [everyone]);
  const count = headcount(living.length, settings.cap);

  const open = useCallback((id: string | null, tab: Tab = "overview") => navigate({ screen: "village", sub: villageSub(id === null ? { agent: null } : { agent: id, tab }) }), [navigate]);

  const dialog = creating ? (
    <CreateAgentDialog
      full={count.full}
      cap={settings.cap}
      onClose={() => setCreating(false)}
      onMeet={(id) => {
        setCreating(false);
        open(id);
      }}
    />
  ) : null;

  if (where.agent !== null) {
    return (
      <div className="village-root w-full">
        <AgentPage
          id={where.agent}
          tab={where.tab}
          everyone={everyone}
          rosterLoaded={agents.isSuccess}
          showVitals={settings.vitals}
          onTab={(tab) => open(where.agent, tab)}
          onBack={() => open(null)}
        />
      </div>
    );
  }

  return (
    <div className="village-root w-full">
      <div className="max-w-[1320px] px-3.5 pb-10 pt-4 @2xl:px-[26px] @2xl:pt-[22px]">
        {agents.isPending ? <p className="text-sm text-text-faint">Loading the Village…</p> : null}
        {agents.isError && !agents.data ? <Note tone="bad">{errorDetail(agents.error)} Check that JimmyHQ is running, then reload the page.</Note> : null}
        {agents.isSuccess && everyone.length === 0 ? <Welcome onCreate={() => setCreating(true)} /> : null}
        {everyone.length > 0 ? <Roster everyone={everyone} living={living} count={count} cap={settings.cap} showVitals={settings.vitals} onCreate={() => setCreating(true)} /> : null}
      </div>
      {dialog}
    </div>
  );
}

/** A new install: nobody lives here yet. */
function Welcome({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center gap-4 py-16 text-center">
      <div className="flex size-16 items-center justify-center rounded-full border border-border bg-surface text-text-faint" aria-hidden="true">
        <Users size={28} strokeWidth={1.5} />
      </div>
      <h1 className="text-lg font-semibold text-text">Nobody lives here yet</h1>
      <p className="text-sm leading-relaxed text-text-muted">
        The Village is where your agents live. Each one works on its own through the day in turns (each one a look, a decision and an action), rests when it is tired and sleeps at
        night, when it sorts the day into memory. An agent is born blank, with a temperament of its own, and names itself after a few nights. You give them priorities, talk with
        them here, and approve anything that leaves the building on the Bench.
      </p>
      <button type="button" className="btn btn-primary" onClick={onCreate}>
        <Plus size={15} aria-hidden="true" />
        Create your first agent
      </button>
    </div>
  );
}

type RosterProps = {
  everyone: AgentSummary[];
  living: AgentSummary[];
  count: { full: boolean; text: string };
  cap: number | null;
  showVitals: boolean;
  onCreate: () => void;
};

function Roster({ everyone, living, count, cap, showVitals, onCreate }: RosterProps) {
  const [filter, setFilter] = useState<Filter>("all");
  const [showRetired, setShowRetired] = useState(false);
  const now = useNow();
  const roles = useRolesQuery();
  const providers = useProvidersQuery();
  const noModel = villageModelProblem(roles.data?.roles, providers.data?.providers);
  const counts = filterCounts(living);
  const retired = everyone.filter((agent) => agent.retired);
  const shown = sortRoster(living.filter((agent) => matchesFilter(agent, filter)));

  return (
    <>
      <header className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-lg font-semibold text-text">Village</h1>
        <span className="text-sm text-text-muted">Who is doing what, and who is waiting on you</span>
        <div className="ml-auto flex items-center gap-2">
          <span className="font-mono text-[11px] text-text-faint">{count.text}</span>
          <button type="button" className="btn btn-primary h-8" onClick={onCreate} disabled={count.full} aria-describedby={count.full ? "village-full" : undefined}>
            <Plus size={15} aria-hidden="true" />
            Create an agent
          </button>
        </div>
      </header>
      {noModel ? (
        <div className="mb-4">
          <Note tone="warn">
            {noModel}{" "}
            <a href="#/settings/models" className="underline underline-offset-2">
              Open Models and roles
            </a>
          </Note>
        </div>
      ) : null}
      {count.full ? (
        <div className="mb-4" id="village-full">
          <Note tone="warn">
            The village is full{cap !== null ? `: it holds at most ${cap} agents` : ""}. To make room, retire an agent from its page, or raise{" "}
            <a href="#/settings/village/village.headcount_cap" className="underline underline-offset-2">
              Most agents at once
            </a>{" "}
            in Settings, Village.
          </Note>
        </div>
      ) : null}

      <div className="village-layout">
        <div className="min-w-0">
          <div className="village-seg mb-3.5" role="group" aria-label="Show agents who are">
            {FILTERS.map((item) => (
              <button key={item} type="button" aria-pressed={filter === item} onClick={() => setFilter(item)}>
                {FILTER_LABELS[item]}
                <span className="n">{counts[item]}</span>
              </button>
            ))}
          </div>
          {living.length === 0 ? (
            <Note>Everyone who lived here has retired. Create an agent to start again; the retired ones are listed below.</Note>
          ) : shown.length === 0 ? (
            <p className="py-6 text-sm text-text-muted">
              Nobody is {FILTER_LABELS[filter].toLowerCase()} right now.{" "}
              <button type="button" className="underline underline-offset-2" onClick={() => setFilter("all")}>
                Show everyone
              </button>
            </p>
          ) : (
            <div className="village-grid">
              {shown.map((agent) => (
                <AgentCard key={agent.id} agent={agent} colour={colourOf(agent.id, everyone)} showVitals={showVitals} now={now} />
              ))}
            </div>
          )}
          {retired.length > 0 ? (
            <div className="mt-6">
              <button type="button" className="text-xs text-text-muted underline underline-offset-2" aria-expanded={showRetired} onClick={() => setShowRetired((value) => !value)}>
                {showRetired ? "Hide" : "Show"} {retired.length} retired {retired.length === 1 ? "agent" : "agents"}
              </button>
              {showRetired ? (
                <div className="village-grid mt-3">
                  {retired.map((agent) => (
                    <AgentCard key={agent.id} agent={agent} colour={colourOf(agent.id, everyone)} showVitals={false} now={now} />
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          <VillageWide agents={everyone} />
        </div>
        <PrioritiesPanel agents={everyone} />
      </div>
    </>
  );
}
