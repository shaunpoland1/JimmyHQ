import { ArrowLeft, Download, History, Trash2 } from "lucide-react";
import { useState } from "react";
import { ApiError } from "../../api";
import { Switch } from "../../components/Switch";
import { formatBytes, formatFullTime } from "../../lib/format";
import { RemoveDialog } from "./RemoveDialog";
import { RollbackDialog } from "./RollbackDialog";
import { UpdateDialog } from "./UpdateDialog";
import { pendingSentence, rollbackChoices } from "./lib/update";
import { feedsHeading } from "./lib/feeds";
import { groupLines } from "./lib/permissions";
import { LIST_HASH, screenHash, settingsHash } from "./lib/route";
import { badgeTone, aboutLines, appState, badges, keptLine, switchLabel, tierWords, whereLines } from "./lib/status";
import { HealthPanel } from "./HealthPanel";
import { InboundPanel } from "./InboundPanel";
import { LocalAddressesPanel } from "./LocalAddressesPanel";
import { RolesPanel } from "./RolesPanel";
import { useAppQuery, useCheckUpdate, useSetEnabled } from "./queries";
import { ErrorCard, LoadingCard, Notice, PermissionGroups, SMALL_BTN, SectionHeading, StatePill, Tag, TierBadge } from "./ui";

/** One app in full: what it is, what it may touch, what the owner agreed to, and where its data is. */
export function AppDetail({ id }: { id: string }) {
  const query = useAppQuery(id);
  const setEnabled = useSetEnabled();
  const [removing, setRemoving] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const check = useCheckUpdate();

  const back = (
    <a href={LIST_HASH} className="mb-3 inline-flex items-center gap-1.5 rounded text-xs text-text-muted hover:text-text">
      <ArrowLeft size={13} aria-hidden="true" />
      All apps
    </a>
  );

  if (query.isPending) {
    return (
      <div>
        {back}
        <LoadingCard>Loading the app…</LoadingCard>
      </div>
    );
  }
  if (query.isError) {
    const gone = query.error instanceof ApiError && query.error.status === 404;
    return (
      <div>
        {back}
        {gone ? (
          <p className="rounded-xl border border-border bg-surface px-4 py-6 text-sm text-text-muted" role="status">
            There is no app called <span className="font-mono">{id}</span>. It may have been removed. Go back to the list of apps.
          </p>
        ) : (
          <ErrorCard what="This app" error={query.error} onRetry={() => void query.refetch()} />
        )}
      </div>
    );
  }

  const app = query.data;
  const state = appState(app);
  const tier = tierWords(app);
  const where = whereLines(app);
  const first = app.screens?.[0];
  const groups = groupLines(app.permissions ?? []);
  const agreed = app.consent;
  const about = aboutLines(app);
  const pending = pendingSentence(app.name, app.update ?? null);
  const earlier = rollbackChoices(app.versions ?? []);
  const feeds = app.feeds ?? [];

  return (
    <div className="@container">
      {back}
      <header className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 basis-60">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h1 className="min-w-0 break-words text-xl font-semibold">{app.name}</h1>
            <span className="font-mono text-xs text-text-muted">{app.version}</span>
            <TierBadge tier={tier.key} bundled={app.provenance === "bundled"} />
            {badges(app).map((text) => (
              <Tag key={text} tone={badgeTone(text)}>
                {text}
              </Tag>
            ))}
          </div>
          {app.summary ? <p className="mt-1 break-words text-sm text-text-muted">{app.summary}</p> : null}
          <p className="mt-1 text-xs text-text-muted">{tier.sentence}</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2.5">
          <StatePill state={state} />
          <span className="sr-only" id="apps-detail-switch">
            {switchLabel(app)}
          </span>
          <Switch checked={app.enabled} labelledBy="apps-detail-switch" disabled={setEnabled.isPending} onChange={(next) => setEnabled.mutate({ app, enabled: next })} />
          {app.enabled && first ? (
            <a className={SMALL_BTN} href={screenHash(app.id, first.id)} aria-label={`Open ${app.name}`}>
              Open
            </a>
          ) : null}
          <button type="button" className={`${SMALL_BTN} text-danger`} onClick={() => setRemoving(true)} aria-label={`Remove ${app.name}`}>
            <Trash2 size={13} aria-hidden="true" />
            Remove
          </button>
        </div>
      </header>

      <div className="mt-3 flex flex-col gap-2">
        {state.reason ? (
          <p className="rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-text" role="status">
            <span className="font-semibold text-danger">Needs attention.</span> {state.reason}
          </p>
        ) : null}
      </div>

      {app.tier === "full" ? <HealthPanel app={app} /> : null}
      {app.tier === "full" ? <RolesPanel id={app.id} name={app.name} /> : null}
      {app.tier === "full" ? <InboundPanel id={app.id} name={app.name} /> : null}
      <LocalAddressesPanel id={app.id} name={app.name} />

      <SectionHeading
        id="apps-detail-versions"
        aside={
          <>
            <button type="button" className={SMALL_BTN} onClick={() => check.mutate(app)} disabled={check.isPending}>
              Look for updates
            </button>
            <button type="button" className={SMALL_BTN} onClick={() => setUpdating(true)}>
              <Download size={13} aria-hidden="true" />
              Update
            </button>
            <button type="button" className={SMALL_BTN} onClick={() => setRollingBack(true)} disabled={earlier.length === 0}>
              <History size={13} aria-hidden="true" />
              Roll back
            </button>
          </>
        }
      >
        Versions
      </SectionHeading>
      <section aria-labelledby="apps-detail-versions" className="flex flex-col gap-2 rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
        {pending ? <Notice tone={app.update?.needsConsent || app.update?.problem ? "warn" : "calm"}>{pending}</Notice> : null}
        {app.environment_note ? <Notice tone="warn">{app.environment_note}</Notice> : null}
        <ul className="flex flex-col gap-1" aria-label="Versions kept">
          {(app.versions ?? []).map((v) => (
            <li key={v.name} className="flex flex-wrap gap-x-2">
              <span className="font-mono">{v.version}</span>
              <span className="text-text-muted">
                {v.current ? "In use." : v.usedMs ? `Used until ${formatFullTime(v.usedMs)}.` : "Kept."}
                {!v.current && v.hasBackup ? " A copy of its data is kept." : ""}
              </span>
            </li>
          ))}
        </ul>
        {earlier.length === 0 ? <p className="text-text-muted">No earlier version is kept yet. After an update, the version before stays here so you can go back.</p> : null}
      </section>

      <SectionHeading id="apps-detail-may">What it may do</SectionHeading>
      <section aria-labelledby="apps-detail-may" className="rounded-xl border border-border bg-surface px-3.5 py-3">
        <PermissionGroups groups={groups} />
        {(app.net_notes ?? []).map((note) => (
          <div key={note} className="mt-2">
            <Notice tone="warn">{note}</Notice>
          </div>
        ))}
      </section>

      <SectionHeading id="apps-detail-agreed">What you agreed to</SectionHeading>
      <section aria-labelledby="apps-detail-agreed" className="rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
        {agreed && agreed.granted_ms !== null ? (
          <>
            <p>
              You agreed on <time dateTime={new Date(agreed.granted_ms).toISOString()}>{formatFullTime(agreed.granted_ms)}</time>
              {agreed.version ? `, for version ${agreed.version}` : ""}.
            </p>
            {agreed.permissions && agreed.permissions.length > 0 ? (
              <ul className="mt-1.5 flex flex-wrap gap-1.5" aria-label="Permissions you agreed to">
                {agreed.permissions.map((permission) => (
                  <li key={permission}>
                    <Tag>{permission}</Tag>
                  </li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="text-text-muted">JimmyHQ has no record of when you agreed to what this app may do.</p>
        )}
      </section>

      {feeds.length > 0 ? (
        <>
          <SectionHeading id="apps-detail-feeds">{feedsHeading(feeds)}</SectionHeading>
          <section aria-labelledby="apps-detail-feeds" className="rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
            <ul className="flex flex-col gap-1" aria-label="Feeds between apps">
              {feeds.map((line) => (
                <li key={line} className="break-words">
                  {line}
                </li>
              ))}
            </ul>
          </section>
        </>
      ) : null}

      <SectionHeading id="apps-detail-about">About this app</SectionHeading>
      <section aria-labelledby="apps-detail-about" className="rounded-xl border border-border bg-surface px-3.5 py-3">
        <dl className="grid gap-x-4 gap-y-1.5 text-[13px] @md:grid-cols-[9rem_1fr]">
          <dt className="text-text-faint">Made by</dt>
          <dd className="break-words">{about.madeBy}</dd>
          {app.licence ? (
            <>
              <dt className="text-text-faint">Licence</dt>
              <dd>{app.licence}</dd>
            </>
          ) : null}
          <dt className="text-text-faint">Review</dt>
          <dd>{about.review}</dd>
          <dt className="text-text-faint">From</dt>
          <dd className="break-all">{about.source}</dd>
          {app.installed_ms ? (
            <>
              <dt className="text-text-faint">Installed</dt>
              <dd>{formatFullTime(app.installed_ms)}</dd>
            </>
          ) : null}
        </dl>
      </section>

      <SectionHeading id="apps-detail-where">Where its screens are</SectionHeading>
      <section aria-labelledby="apps-detail-where" className="rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
        {where.length === 0 ? (
          <p className="text-text-muted">It adds no screens.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {where.map((text) => (
              <li key={text} className="break-words">
                {text}
              </li>
            ))}
          </ul>
        )}
      </section>

      {app.has_settings ? (
        <>
          <SectionHeading id="apps-detail-settings">Its settings</SectionHeading>
          <section aria-labelledby="apps-detail-settings" className="rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
            <p className="text-text-muted">This app has settings of its own, kept with the rest of your settings.</p>
            <a href={settingsHash(app)} className={`${SMALL_BTN} mt-2`}>
              Open {app.name} settings
            </a>
          </section>
        </>
      ) : null}

      <SectionHeading id="apps-detail-data">Its data</SectionHeading>
      <section aria-labelledby="apps-detail-data" className="rounded-xl border border-border bg-surface px-3.5 py-3 text-[13px]">
        <p>{app.data_bytes !== null && app.data_bytes !== undefined ? `It takes ${formatBytes(app.data_bytes)} on this computer.` : "JimmyHQ could not tell how much space its data takes."}</p>
        <p className="mt-1 text-text-muted">{keptLine(app)}</p>
      </section>

      {updating ? <UpdateDialog app={app} onClose={() => setUpdating(false)} /> : null}
      {rollingBack ? <RollbackDialog app={app} onClose={() => setRollingBack(false)} /> : null}
      {removing ? <RemoveDialog app={app} onClose={() => setRemoving(false)} onRemoved={() => (window.location.hash = LIST_HASH)} /> : null}
    </div>
  );
}
