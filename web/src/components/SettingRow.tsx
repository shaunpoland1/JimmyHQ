import { RotateCcw } from "lucide-react";
import type { Setting, SettingValue } from "../api";
import { asStringList, coerceSettingValue, describeValue, isDefaultValue } from "../lib/settings";
import { settingControlId, settingRowId } from "../lib/settingsNav";
import { zoneName } from "../lib/timeZones";
import { ListControl } from "./ListControl";
import { NumberControl } from "./NumberControl";
import { Switch } from "./Switch";
import { TextControl } from "./TextControl";
import { TimeZoneControl } from "./TimeZoneControl";

type SettingRowProps = {
  setting: Setting;
  onSave: (key: string, value: SettingValue) => void;
  onReset: (key: string, defaultValue: SettingValue) => void;
  /** True for a moment after a link brought the reader to this setting. */
  highlighted?: boolean;
};

/** One setting: label, help, the control for its type, default hint and reset. */
export function SettingRow({ setting, onSave, onReset, highlighted = false }: SettingRowProps) {
  const id = settingControlId(setting.key);
  const timeZone = setting.type === "str" && setting.format === "timezone";
  const labelId = `${id}-label`;
  const helpId = `${id}-help`;
  const describedBy = setting.help ? helpId : undefined;
  const changed = !isDefaultValue(setting);
  // Text and list controls need the full width; the rest sit beside the label.
  const wide = setting.type === "str" || setting.type === "list";
  const save = (value: SettingValue) => onSave(setting.key, value);

  let control;
  switch (setting.type) {
    case "bool":
      control = <Switch id={id} checked={setting.value === true} onChange={save} labelledBy={labelId} describedBy={describedBy} />;
      break;
    case "choice":
      control = (
        <select
          id={id}
          className="input w-auto max-w-full pr-8"
          value={String(setting.value)}
          aria-describedby={describedBy}
          onChange={(event) => {
            const result = coerceSettingValue(setting, event.target.value);
            if (result.ok) save(result.value);
          }}
        >
          {(setting.choices ?? []).map((choice) => (
            <option key={String(choice.value)} value={String(choice.value)}>
              {choice.label}
            </option>
          ))}
        </select>
      );
      break;
    case "int":
    case "float":
      control = <NumberControl id={id} setting={setting} describedBy={describedBy} onSave={save} />;
      break;
    case "str":
      control = timeZone ? (
        <TimeZoneControl id={id} value={String(setting.value ?? "")} describedBy={describedBy} onSave={save} />
      ) : (
        <TextControl id={id} value={String(setting.value ?? "")} describedBy={describedBy} onSave={save} />
      );
      break;
    case "list":
      control = (
        <ListControl
          id={id}
          label={setting.label}
          items={asStringList(setting.value)}
          describedBy={describedBy}
          onSave={save}
          options={setting.items?.map((item) => ({ value: String(item.value), label: item.label }))}
          unique={setting.unique}
        />
      );
      break;
    default:
      control = <p className="text-xs text-text-faint">This setting type is not supported yet.</p>;
  }

  return (
    <li
      id={settingRowId(setting.key)}
      tabIndex={-1}
      data-highlighted={highlighted || undefined}
      className={`flex scroll-mt-4 gap-3 px-4 py-3.5 outline-none transition-[background-color,box-shadow] duration-700 first:rounded-t-xl last:rounded-b-xl motion-reduce:transition-none data-[highlighted=true]:bg-accent/10 data-[highlighted=true]:shadow-[inset_3px_0_0_var(--accent)] ${wide ? "flex-col" : "flex-col @md:flex-row @md:items-start @md:justify-between @md:gap-6"}`}
    >
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <label id={labelId} htmlFor={id} className="text-[13px] font-medium text-text">
            {setting.label}
          </label>
          {changed ? <span className="rounded bg-accent/10 px-1.5 py-px font-mono text-[10px] uppercase tracking-wide text-accent">Changed</span> : null}
          {setting.advanced ? <span className="rounded bg-surface-2 px-1.5 py-px font-mono text-[10px] uppercase tracking-wide text-text-faint">Advanced</span> : null}
        </p>
        {setting.help ? (
          <p id={helpId} className="mt-0.5 text-xs text-text-muted">
            {setting.help}
          </p>
        ) : null}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-faint">
          <span className="min-w-0 break-words">Default: {timeZone && setting.default === "" ? "the computer JimmyHQ runs on" : timeZone ? zoneName(String(setting.default)) : describeValue(setting, setting.default)}</span>
          {changed ? (
            <button
              type="button"
              onClick={() => onReset(setting.key, setting.default)}
              aria-label={`Reset ${setting.label} to default`}
              className="inline-flex items-center gap-1 rounded text-accent hover:underline"
            >
              <RotateCcw size={11} aria-hidden="true" />
              Reset
            </button>
          ) : null}
        </p>
      </div>
      <div className={wide ? "w-full" : "flex-none"}>{control}</div>
    </li>
  );
}
