import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import { DOCS_URL, GITHUB_URL } from "../config";
import { useModelIndicators } from "../modules/models/useModelIndicators";

const POLL_MS = 15_000;

/** Slim bottom bar: connection status on the left, version and links on the right. */
export function StatusBar() {
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: true,
    retry: false,
  });
  const indicators = useModelIndicators();
  const [pinned, setPinned] = useState(false);
  const [hovered, setHovered] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const popoverId = useId();
  const open = pinned || hovered;

  useEffect(() => {
    if (!pinned) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapper.current?.contains(event.target as Node)) setPinned(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPinned(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [pinned]);

  const online = status.isSuccess && !status.isRefetchError;
  const processes = online ? (status.data?.processes ?? []) : [];
  const degraded = processes.some((process) => !process.ok);
  const tone = !online ? "bg-danger" : degraded ? "bg-warning" : "bg-success";
  const label = status.isPending ? "Connecting…" : online ? "Online" : "Offline";

  return (
    <footer className="flex h-7 flex-none items-center justify-between gap-3 border-t border-border bg-bg px-3 text-[11px] text-text-muted">
      <div ref={wrapper} className="relative" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
        <button
          type="button"
          onClick={() => setPinned((value) => !value)}
          aria-expanded={open}
          aria-controls={popoverId}
          aria-label={`${label}. Show process status`}
          className="flex h-6 items-center gap-1.5 rounded px-1 hover:text-text"
        >
          <span className={`dot ${status.isPending ? "bg-sage" : tone}`} aria-hidden="true" />
          <span role="status">{label}</span>
        </button>
        {open ? (
          <div
            id={popoverId}
            role="group"
            aria-label="Processes"
            className="absolute bottom-full left-0 z-30 mb-1 w-56 max-w-[calc(100vw-1.5rem)] rounded-lg border border-border bg-surface p-2 shadow-pop"
          >
            {processes.length === 0 ? (
              <p className="px-1 py-0.5 text-text-faint">{online ? "No processes reported." : "The server cannot be reached."}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {processes.map((process) => (
                  <li key={process.name} className="flex items-center gap-2 px-1">
                    <span className={`dot ${process.ok ? "bg-success" : "bg-danger"}`} aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate font-mono text-text">{process.name}</span>
                    <span className="flex-none text-text-faint">{process.ok ? "running" : "not responding"}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : null}
      </div>
      <ul className="flex min-w-0 flex-1 items-center gap-3 overflow-hidden" aria-label="Connections">
        {indicators.map((item) => (
          <li key={item.id} className="flex min-w-0 items-center gap-1.5">
            <span className={`dot ${item.ok ? "bg-success" : "bg-danger"}`} aria-hidden="true" />
            <span className="truncate">{item.label}</span>
          </li>
        ))}
      </ul>
      <div className="flex min-w-0 items-center gap-3">
        {status.data?.version ? (
          <span className="truncate font-mono text-text-faint">v{status.data.version.replace(/^v/i, "")}</span>
        ) : null}
        {/* Its own tab: the Wall is for a second screen, and leaving this page
            would stop a reply being written and drop unsent drafts. */}
        <a href="/wall" target="_blank" rel="noopener" className="rounded hover:text-text hover:underline">
          Wall
        </a>
        <a href={GITHUB_URL} target="_blank" rel="noreferrer noopener" className="rounded hover:text-text hover:underline">
          GitHub
        </a>
        <a href={DOCS_URL} target="_blank" rel="noreferrer noopener" className="rounded hover:text-text hover:underline">
          Docs
        </a>
      </div>
    </footer>
  );
}
