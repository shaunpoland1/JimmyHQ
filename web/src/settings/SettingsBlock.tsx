import { ChevronDown } from "lucide-react";
import type { Setting, SettingValue } from "../api";
import { SettingRow } from "../components/SettingRow";
import { advancedToggleLabel, splitAdvanced } from "../lib/settingsAdvanced";
import { blockHeadingId } from "../lib/settingsNav";
import type { PageBlock } from "../lib/settingsNav";

type SettingsBlockProps = {
  block: PageBlock<Setting>;
  /** Whether this block's advanced settings are open. */
  advancedOpen: boolean;
  onAdvancedOpen: (open: boolean) => void;
  /** The setting a link brought the reader to: drawn even when it is advanced and they are closed. */
  revealKey: string | null;
  highlighted: string | null;
  onSave: (key: string, value: SettingValue) => void;
  onReset: (key: string, defaultValue: SettingValue) => void;
};

/**
 * One headed block of a settings page: its heading, its settings, and one
 * quiet control for the advanced ones. A changed setting is always drawn.
 */
export function SettingsBlock({ block, advancedOpen, onAdvancedOpen, revealKey, highlighted, onSave, onReset }: SettingsBlockProps) {
  const split = splitAdvanced(block.rows, advancedOpen, revealKey);
  const toggle = advancedToggleLabel(split, advancedOpen);
  const listId = `${blockHeadingId(block.id)}-list`;
  return (
    <>
      {block.label ? (
        <div className="mb-3">
          <h3 id={blockHeadingId(block.id)} tabIndex={-1} className="scroll-mt-6 text-sm font-semibold outline-none">
            {block.label}
          </h3>
          {block.description ? <p className="mt-0.5 text-xs text-text-muted">{block.description}</p> : null}
        </div>
      ) : null}
      {split.shown.length > 0 ? (
        <ul id={listId} aria-labelledby={block.label ? blockHeadingId(block.id) : "settings-section-heading"} className="@container divide-y divide-border rounded-xl border border-border bg-surface">
          {split.shown.map((setting) => (
            <SettingRow key={setting.key} setting={setting} highlighted={highlighted === setting.key} onSave={onSave} onReset={onReset} />
          ))}
        </ul>
      ) : (
        <p id={listId} className="rounded-xl border border-dashed border-border px-4 py-3 text-xs text-text-muted">
          Everything here is fine tuning that works well as it is.
        </p>
      )}
      {toggle ? (
        <button
          type="button"
          aria-expanded={advancedOpen}
          aria-controls={listId}
          onClick={() => onAdvancedOpen(!advancedOpen)}
          className="mt-2 inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs text-text-muted hover:text-text"
        >
          <ChevronDown size={13} aria-hidden="true" className={`transition-transform motion-reduce:transition-none ${advancedOpen ? "rotate-180" : ""}`} />
          {toggle}
        </button>
      ) : null}
    </>
  );
}
