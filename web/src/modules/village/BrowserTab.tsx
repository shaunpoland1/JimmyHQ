import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { errorDetail } from "../../api";
import { villageApi } from "./api";
import type { AgentCard, BrowserChange, BrowserState, SubmitLevel } from "./api";
import { browserKey, useBrowser } from "./hooks";
import { LEVELS, LEVEL_HELP, LEVEL_LABELS, badHost, frontTab, parseHosts, pictureUrl, refusalWords, sameHosts, standing, useLine } from "./lib/browser";
import { displayName } from "./lib/status";
import { Note, Section, clockTime, dateTime } from "./parts";

/** The agent's Browser tab: the owner watches what its browser shows, and chooses how far it may go. The owner does not drive. */
export function BrowserTab({ agent }: { agent: AgentCard }) {
  const name = displayName(agent);
  const query = useBrowser(agent.id);
  const state = query.data;

  if (query.isPending) return <p className="text-sm text-text-faint">Loading {name}'s browser…</p>;
  if (!state) {
    return <Note tone="bad">{errorDetail(query.error)} Only the owner can watch an agent's browser: sign in as the owner, then reload the page.</Note>;
  }
  return (
    <div className="flex flex-col gap-3.5">
      <Note tone={state.held && !state.ready ? "warn" : undefined} live>
        {standing(state, name)}
      </Note>
      {state.held && !state.ready && state.problem ? <Note tone="warn">{state.problem}</Note> : null}
      {query.isError ? <Note tone="warn">The latest read failed ({errorDetail(query.error)}), so this may be behind. It tries again by itself.</Note> : null}
      <Watching state={state} name={name} />
      <Limits state={state} name={name} />
      <Choices state={state} name={name} retired={agent.retired} />
    </div>
  );
}

function Watching({ state, name }: { state: BrowserState; name: string }) {
  const front = frontTab(state.tabs);
  const picture = state.running ? pictureUrl(state.agent_id, state.picture_ms) : null;
  return (
    <Section title="What it is looking at">
      {!state.running || !front ? (
        <p className="text-sm text-text-muted">Nothing right now. When {name} opens a page, it shows here.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <ol className="flex flex-col gap-1" aria-label="Open tabs">
            {state.tabs.map((tab, at) => (
              <li key={`${at}-${tab.url}`} className="village-browser-tab" aria-current={tab.active ? "true" : undefined}>
                <span className="text-xs text-text-faint">{at + 1}</span>
                <span className="min-w-0">
                  <span className="block truncate text-sm text-text">{tab.title || "(no title)"}</span>
                  <span className="block truncate font-mono text-xs text-text-muted">{tab.url}</span>
                </span>
              </li>
            ))}
          </ol>
          {picture ? (
            <figure className="village-browser-shot">
              <img src={picture} alt={`${name}'s browser, showing ${front.title || front.url}`} />
              <figcaption className="text-xs text-text-faint">
                As it looked at {state.picture_ms ? clockTime(state.picture_ms) : "its last action"}. A new picture replaces this one after each action; none is kept.
              </figcaption>
            </figure>
          ) : (
            <p className="text-sm text-text-muted">No picture: pictures are switched off under Settings, Browser, or the last one could not be taken.</p>
          )}
          <p className="text-xs text-text-faint">You are watching, not driving: nothing on this tab clicks or types in {name}'s browser.</p>
        </div>
      )}
    </Section>
  );
}

function Limits({ state, name }: { state: BrowserState; name: string }) {
  const rows = [
    { label: "Pages opened", use: useLine(state.limits?.pages) },
    { label: "Clicks, typing and other actions", use: useLine(state.limits?.actions) },
    { label: "Forms it asked you to approve", use: useLine(state.limits?.requests) },
  ];
  const refused = state.refused_last ?? [];
  const waiting = state.waiting ?? [];
  return (
    <Section title="Today, and what was refused">
      <dl className="village-browser-limits">
        {rows.map((row) =>
          row.use ? (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd data-spent={row.use.spent || undefined}>
                {row.use.text}
                {row.use.spent ? " (used up until tomorrow)" : ""}
              </dd>
            </div>
          ) : null,
        )}
      </dl>
      <p className="mt-2 text-xs text-text-faint">The limits are under Settings, Browser, and apply to every agent.</p>
      {waiting.length > 0 ? (
        <Note tone="warn">
          {name} is waiting for you on {waiting.length} {waiting.length === 1 ? "form" : "forms"}
          {waiting[0]?.host ? ` (at ${waiting[0].host}${waiting.length > 1 ? " and elsewhere" : ""})` : ""}. Nothing is sent until you approve it on the Bench.
        </Note>
      ) : null}
      {state.running && refused.length > 0 ? (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-text">
            {state.refused ?? refused.length} {(state.refused ?? refused.length) === 1 ? "request" : "requests"} refused by the fence since the browser opened
          </summary>
          <ul className="mt-2 flex flex-col gap-1 text-xs text-text-muted">
            {refused
              .slice()
              .reverse()
              .map((item, at) => (
                <li key={`${item.ts_ms}-${at}`} className="[overflow-wrap:anywhere]">
                  <time dateTime={new Date(item.ts_ms).toISOString()}>{dateTime(item.ts_ms)}</time> · {refusalWords(item)}
                </li>
              ))}
          </ul>
          <p className="mt-2 text-xs text-text-faint">Most of these are parts of a page (adverts, sign-in buttons, trackers), not somewhere {name} tried to go.</p>
        </details>
      ) : null}
    </Section>
  );
}

function Choices({ state, name, retired }: { state: BrowserState; name: string; retired: boolean }) {
  const client = useQueryClient();
  const [hosts, setHosts] = useState(state.allow_hosts.join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const kept = state.allow_hosts.join("\n");
  // When the saved list changes underneath (another window), show it, unless the owner is mid-edit.
  useEffect(() => setHosts(kept), [kept]);

  const typed = parseHosts(hosts);
  const wrong = badHost(typed);
  const dirty = !sameHosts(typed, state.allow_hosts);

  const change = async (body: BrowserChange, done: string) => {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      client.setQueryData(browserKey(state.agent_id), await villageApi.changeBrowser(state.agent_id, body));
      setSaved(done);
    } catch (e) {
      setError(errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section title="How far it may go">
      <fieldset className="flex flex-col gap-2" disabled={retired || saving}>
        <legend className="mb-1 text-sm text-text">When a form would change something (buy, sign up, send, delete, confirm)</legend>
        {LEVELS.map((level: SubmitLevel) => (
          <label key={level} className="village-browser-level">
            <input
              type="radio"
              name={`browser-level-${state.agent_id}`}
              checked={state.submit_level === level}
              onChange={() => void change({ submit_level: level }, `Saved: ${LEVEL_LABELS[level].toLowerCase()}.`)}
            />
            <span>
              <span className="block text-sm font-medium text-text">{LEVEL_LABELS[level]}</span>
              <span className="block text-xs text-text-muted">{LEVEL_HELP[level]}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <p className="mt-2 text-xs text-text-faint">
        Searching and reading need no leave. Passwords, email addresses, card numbers and codes are never typed, whatever you choose here: that list is under Settings, Browser.
      </p>

      <div className="mt-4 flex flex-col gap-1.5">
        <label htmlFor={`browser-hosts-${state.agent_id}`} className="text-sm text-text">
          Only these sites (leave empty for anywhere the fence allows)
        </label>
        <textarea
          id={`browser-hosts-${state.agent_id}`}
          className="village-textarea min-h-[4.5rem] font-mono text-xs"
          value={hosts}
          onChange={(event) => setHosts(event.target.value)}
          placeholder={"example.com\n*.example.org"}
          spellCheck={false}
          disabled={retired || saving}
          aria-describedby={`browser-hosts-help-${state.agent_id}`}
        />
        <p id={`browser-hosts-help-${state.agent_id}`} className="text-xs text-text-faint">
          One site to a line. A name covers the site and everything under it. With a list here, {name}'s browser can reach nothing else, not even the pictures and scripts a listed page loads from other sites. Your deny list and the private-network rule still apply.
        </p>
        {wrong ? <Note tone="warn">"{wrong}" is not a site's name on its own. Write it like example.com: no http://, path or port.</Note> : null}
        <div>
          <button
            type="button"
            className="btn h-7 px-2.5 text-xs"
            disabled={retired || saving || !dirty || wrong !== null}
            onClick={() => void change({ allow_hosts: typed }, typed.length ? "Saved the list of sites." : "Saved: no list, so anywhere the fence allows.")}
          >
            {saving ? "Saving…" : "Save the list"}
          </button>
        </div>
      </div>
      {error ? <Note tone="bad">{error}</Note> : null}
      {saved && !error ? (
        <Note tone="good" live>
          {saved}
        </Note>
      ) : null}
      {retired ? <p className="mt-2 text-xs text-text-faint">{name} has retired, so nothing here can be changed.</p> : null}
    </Section>
  );
}
