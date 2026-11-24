import { LayoutGrid } from "lucide-react";
import { useState } from "react";
import { BuildDialog } from "./BuildDialog";
import { FirstPartyApps } from "./FirstPartyApps";
import type { InstallSource } from "./api";
import { EMPTY_ABOUT, EMPTY_TRY, EXAMPLES, TIER_SENTENCES, exampleSource } from "./lib/examples";
import { TierBadge } from "./ui";

type Props = { onInstall: (source?: InstallSource) => void };

/**
 * What to show before any app is installed. Two panels: on the left, quietly, what the
 * three kinds of app are; on the right, on a raised surface, the examples to try.
 * Side by side when there is room, one above the other in a narrow column.
 */
export function EmptyApps({ onInstall }: Props) {
  const [building, setBuilding] = useState(false);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col items-center gap-2 rounded-xl border border-border bg-surface px-5 pb-6 pt-8 text-center">
        <div className="flex size-16 items-center justify-center rounded-full border border-border bg-bg text-text-faint" aria-hidden="true">
          <LayoutGrid size={28} strokeWidth={1.5} />
        </div>
        <h2 className="text-lg font-semibold">No apps yet</h2>
        <p className="max-w-md text-sm text-text-muted">An app adds a screen, a tile or a skill to JimmyHQ.</p>
        <div className="mt-2 flex flex-wrap justify-center gap-2">
          <button type="button" className="btn btn-primary" onClick={() => onInstall()}>
            Install an app
          </button>
          <button type="button" className="btn" onClick={() => setBuilding(true)}>
            Build your own
          </button>
        </div>
      </div>

      <div className="grid items-stretch gap-4 @2xl:grid-cols-2">
        <section aria-labelledby="apps-kinds-heading" className="rounded-xl border border-dashed border-border px-4 py-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-text-faint">{EMPTY_ABOUT.eyebrow}</p>
          <h3 id="apps-kinds-heading" className="mb-1 mt-0.5 text-[13px] font-semibold">
            {EMPTY_ABOUT.heading}
          </h3>
          <p className="mb-3 text-[13px] text-text-muted">{EMPTY_ABOUT.text}</p>
          <ul className="flex flex-col gap-3">
            {TIER_SENTENCES.map((item) => (
              <li key={item.key} className="flex flex-col gap-1">
                <TierBadge tier={item.key} />
                <p className="text-[13px] text-text-muted">{item.text}</p>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="apps-try-heading" className="rounded-xl border border-border bg-surface px-4 py-4">
          <p className="text-[11px] font-medium uppercase tracking-wide text-accent">{EMPTY_TRY.eyebrow}</p>
          <h3 id="apps-try-heading" className="mb-1 mt-0.5 text-[13px] font-semibold">
            {EMPTY_TRY.heading}
          </h3>
          <p className="mb-3 text-[13px] text-text-muted">{EMPTY_TRY.text}</p>
          <ul className="flex flex-col gap-2">
            {EXAMPLES.map((example) => (
              <li key={example.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-bg px-3 py-2">
                <div className="min-w-0 flex-1 basis-40">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {example.name} <TierBadge tier={example.tier} />
                  </p>
                  <p className="text-xs text-text-muted">{example.blurb}</p>
                </div>
                <button type="button" className="btn btn-primary h-7 px-2.5 text-xs" onClick={() => onInstall(exampleSource(example))} aria-label={`Try ${example.name}`}>
                  Try it
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
      <FirstPartyApps installedIds={[]} onInstall={onInstall} />
      {building ? <BuildDialog onClose={() => setBuilding(false)} /> : null}
    </div>
  );
}
