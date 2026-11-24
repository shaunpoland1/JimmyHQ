import { Copy, ExternalLink } from "lucide-react";
import { GITHUB_URL } from "../../config";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { BUILD_INTRO, BUILD_KINDS, DESCRIBED_EXAMPLE, GUIDE_LINK_NOTE, GUIDE_LINK_TEXT, MANIFEST_FILE, guideUrl } from "./lib/build";
import { SMALL_BTN, TierBadge } from "./ui";

/** "Build your own": the three ways to make an app and the first step for each, inside JimmyHQ. */
export function BuildDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const copy = () => {
    navigator.clipboard.writeText(DESCRIBED_EXAMPLE).then(
      () => toast.success(`Copied. Save it as ${MANIFEST_FILE} in a new folder.`),
      () => toast.error("It could not be copied. Select the text and copy it by hand."),
    );
  };

  return (
    <Modal title="Build your own app" onClose={onClose} panelClass="max-h-[88vh] w-full max-w-2xl">
      <div className="@container min-h-0 flex-1 overflow-y-auto px-4 py-4">
        <p className="text-sm text-text-muted">{BUILD_INTRO}</p>
        <ol className="mt-4 flex flex-col gap-4">
          {BUILD_KINDS.map((kind, index) => (
            <li key={kind.key} className="rounded-lg border border-border bg-surface px-3.5 py-3" aria-labelledby={`apps-build-${kind.key}`}>
              <div className="flex flex-wrap items-center gap-2">
                <h3 id={`apps-build-${kind.key}`} className="min-w-0 text-[13px] font-semibold">
                  {index + 1}. {kind.heading}
                </h3>
                <TierBadge tier={kind.key} />
              </div>
              <p className="mt-1 text-[13px] text-text-muted">{kind.what}</p>
              <ol className="mt-2 flex list-decimal flex-col gap-1 pl-5 text-[13px]">
                {kind.steps.map((step) => (
                  <li key={step} className="break-words">
                    {step}
                  </li>
                ))}
              </ol>
              {kind.key === "described" ? (
                <div className="mt-3">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <p className="font-mono text-xs text-text-muted">{MANIFEST_FILE}</p>
                    <button type="button" className={`${SMALL_BTN} ml-auto`} onClick={copy}>
                      <Copy size={13} aria-hidden="true" />
                      Copy
                    </button>
                  </div>
                  <pre className="max-h-72 overflow-auto rounded-lg border border-border bg-bg px-3 py-2 font-mono text-[11.5px] leading-relaxed text-text" tabIndex={0} aria-label={`An example ${MANIFEST_FILE}`}>
                    {DESCRIBED_EXAMPLE}
                  </pre>
                </div>
              ) : null}
              {kind.caveat ? <p className="mt-2 rounded-lg border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-text">{kind.caveat}</p> : null}
            </li>
          ))}
        </ol>
        <p className="mt-4 text-xs text-text-muted">
          <a href={guideUrl(GITHUB_URL)} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium text-accent underline">
            {GUIDE_LINK_TEXT}
            <ExternalLink size={12} aria-hidden="true" />
          </a>
          <span className="ml-1">{GUIDE_LINK_NOTE}</span>
        </p>
      </div>
      <div className="flex justify-end border-t border-border px-4 py-3">
        <button type="button" className="btn btn-primary" onClick={onClose} data-autofocus>
          Done
        </button>
      </div>
    </Modal>
  );
}
