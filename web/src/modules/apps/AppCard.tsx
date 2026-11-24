import { ChevronRight, Trash2 } from "lucide-react";
import { useId } from "react";
import { Switch } from "../../components/Switch";
import type { AppSummary } from "./api";
import { askedSummary } from "./lib/permissions";
import { detailHash, screenHash } from "./lib/route";
import { badgeTone, appState, badges, switchLabel, tierKey, whereLines } from "./lib/status";
import { PermissionSentence, SMALL_BTN, StatePill, Tag, TierBadge } from "./ui";

type Props = {
  app: AppSummary;
  /** True while this app's switch is being saved. */
  saving: boolean;
  onToggle: (app: AppSummary, enabled: boolean) => void;
  onRemove: (app: AppSummary) => void;
  /** Opens the update dialog for an update that was found. */
  onUpdate?: (app: AppSummary) => void;
};

/** One installed app: what it is, whether it is on, where it shows up, and what it may touch. */
export function AppCard({ app, saving, onToggle, onRemove, onUpdate }: Props) {
  const nameId = useId();
  const state = appState(app);
  const tier = tierKey(app);
  const lines = app.permissions ?? [];
  const where = whereLines(app);
  const first = app.screens?.[0];
  const canOpen = app.enabled && first;

  return (
    <li>
      <article className="@container rounded-xl border border-border bg-surface" aria-labelledby={nameId}>
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2 px-3.5 py-3">
          <div className="min-w-0 flex-1 basis-56">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h2 id={nameId} className="min-w-0 break-words text-[15px] font-semibold">
                <a href={detailHash(app.id)} className="rounded hover:underline">
                  {app.name}
                </a>
              </h2>
              <span className="font-mono text-[11px] text-text-muted">{app.version}</span>
              <TierBadge tier={tier} bundled={app.provenance === "bundled"} />
              {badges(app).map((text) => (
                <Tag key={text} tone={badgeTone(text)}>
                  {text}
                </Tag>
              ))}
            </div>
            {app.summary ? <p className="mt-0.5 break-words text-[13px] text-text-muted">{app.summary}</p> : null}
          </div>

          <div className="ml-auto flex flex-none items-center gap-2.5">
            <StatePill state={state} />
            <span className="inline-flex items-center" title={app.enabled ? "Switch off" : "Switch on"}>
              <span id={`${nameId}-switch`} className="sr-only">
                {switchLabel(app)}
              </span>
              <Switch checked={app.enabled} labelledBy={`${nameId}-switch`} disabled={saving} onChange={(next) => onToggle(app, next)} />
            </span>
          </div>
        </div>

        {state.reason ? (
          <p className="mx-3.5 mb-2 break-words rounded-lg border border-danger/40 bg-danger/10 px-3 py-2 text-xs text-text" role="status">
            <span className="font-semibold text-danger">Needs attention.</span> {state.reason}
          </p>
        ) : null}

        <div className="border-t border-border/60 px-3.5 py-2.5">
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-text-faint">{askedSummary(lines)}</p>
          {lines.length === 0 ? (
            <p className="text-xs text-text-faint">The server did not list what this app may do. Open its details to check.</p>
          ) : (
            <ul className="flex flex-col gap-1.5" aria-label={`What ${app.name} may do`}>
              {lines.map((line) => (
                <PermissionSentence key={line.id} line={line} />
              ))}
            </ul>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border/60 px-3.5 py-2.5">
          <div className="min-w-0 flex-1 basis-48 text-xs text-text-muted">
            {where.length === 0 ? (
              <p>Adds no screens.</p>
            ) : (
              <ul>
                {where.map((text) => (
                  <li key={text} className="break-words">
                    {text}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {canOpen && first ? (
              <a className={SMALL_BTN} href={screenHash(app.id, first.id)} aria-label={`Open ${app.name}`}>
                Open
              </a>
            ) : null}
            {app.update_available && onUpdate ? (
              <button type="button" className={SMALL_BTN} onClick={() => onUpdate(app)} aria-label={`Update ${app.name}`}>
                Update
              </button>
            ) : null}
            <a className={SMALL_BTN} href={detailHash(app.id)} aria-label={`Details of ${app.name}`}>
              Details
              <ChevronRight size={13} aria-hidden="true" />
            </a>
            <button type="button" className={`${SMALL_BTN} text-danger`} onClick={() => onRemove(app)} aria-label={`Remove ${app.name}`}>
              <Trash2 size={13} aria-hidden="true" />
              Remove
            </button>
          </div>
        </div>
      </article>
    </li>
  );
}
