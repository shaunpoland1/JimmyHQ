import type { InstallSource } from "./api";
import { FIRST_PARTY_LIST, exampleSource, notInstalled } from "./lib/examples";
import { useFirstPartyQuery } from "./queries";
import { TierBadge } from "./ui";

type Props = { installedIds: readonly string[]; onInstall: (source: InstallSource) => void };

/** The first-party apps not installed yet, in a short list of their own. Nothing when all are installed. */
export function FirstPartyApps({ installedIds, onInstall }: Props) {
  // Nothing while it loads, and nothing when it cannot be read: the list is a convenience,
  // and an app can still be installed from a folder or an address.
  const shipped = useFirstPartyQuery();
  const apps = notInstalled(shipped.data ?? [], installedIds);
  if (apps.length === 0) return null;
  return (
    <section aria-labelledby="apps-first-party-heading" className="rounded-xl border border-border bg-surface px-4 py-4">
      <h3 id="apps-first-party-heading" className="mb-1 text-[13px] font-semibold">
        {FIRST_PARTY_LIST.heading}
      </h3>
      <p className="mb-3 text-[13px] text-text-muted">{FIRST_PARTY_LIST.text}</p>
      <ul className="flex flex-col gap-2">
        {apps.map((app) => (
          <li key={app.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-bg px-3 py-2">
            <div className="min-w-0 flex-1 basis-40">
              <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
                {app.name} <TierBadge tier={app.tier} bundled />
              </p>
              <p className="text-xs text-text-muted">{app.blurb}</p>
            </div>
            <button type="button" className="btn btn-primary h-7 px-2.5 text-xs" onClick={() => onInstall(exampleSource(app))} aria-label={`Install ${app.name}`}>
              Install
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
