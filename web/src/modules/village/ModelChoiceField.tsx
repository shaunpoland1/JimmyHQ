import { useId, useMemo } from "react";
import { ModelPicker } from "../models/ModelPicker";
import type { ModelPickerOptions } from "../models/ModelPicker";
import { useModelPickerOptions } from "../models/useModelPickerOptions";
import type { ModelChoice } from "./api";
import { describeChoice } from "./lib/commands";
import type { ChoiceInfo, Target } from "./lib/commands";

/**
 * The roles and models an agent may use, from the models module's own picker
 * options. The embeddings role is left out (it cannot hold a conversation), and
 * so is the effort control, which an agent's setup does not have.
 */
export function useAgentModelOptions(): { options: ModelPickerOptions; loading: boolean } {
  const { options, loading } = useModelPickerOptions();
  const trimmed = useMemo(() => ({ ...options, roles: options.roles.filter((role) => role.role !== "embeddings"), efforts: [] }), [options]);
  return { options: trimmed, loading };
}

export function choiceInfo(choice: ModelChoice | null | undefined, fallback: Target, options: ModelPickerOptions): ChoiceInfo {
  return describeChoice(choice, fallback, options.roles, options.providers);
}

type Props = {
  label: string;
  hint: string;
  value: Target;
  onChange: (target: Target) => void;
  options: ModelPickerOptions;
  disabled?: boolean;
};

/** One labelled model choice: a role from the Models screen, or one provider's model. */
export function ModelChoiceField({ label, hint, value, onChange, options, disabled }: Props) {
  const id = useId();
  return (
    <div className="flex flex-col gap-1.5" role="group" aria-labelledby={`${id}-label`} aria-describedby={`${id}-hint`}>
      <span id={`${id}-label`} className="text-xs font-medium text-text">
        {label}
      </span>
      <ModelPicker value={{ target: value, effort: null }} onChange={(next) => onChange(next.target)} options={options} disabled={disabled} placement="bottom" />
      <p id={`${id}-hint`} className="text-xs text-text-faint">
        {hint}
      </p>
    </div>
  );
}

/** A model choice read back in words, with where it runs. */
export function ChoiceLine({ info }: { info: ChoiceInfo }) {
  const where = info.onMachine === null ? null : info.onMachine ? "runs on your machines" : "hosted";
  return (
    <span>
      {info.label} <span className="text-text-muted">({info.detail}{where ? `, ${where}` : ""})</span>
    </span>
  );
}
