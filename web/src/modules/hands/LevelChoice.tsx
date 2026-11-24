import { useId } from "react";
import { LEVELS } from "./api";
import type { Effect, Level } from "./api";
import { LEVEL_LABELS, cautiousAloneNote, levelExplanation } from "./lib/wording";

type Props = {
  effect: Effect;
  value: Level;
  onChange: (level: Level) => void;
  /** What the server said about a level, by level: shown next to that choice. */
  refused: Partial<Record<Level, string>>;
  /** The owner's list is careful with this kind of action: "Alone" then carries one sentence saying what it means. */
  cautious?: boolean;
};

/** The three levels as clearly explained choices, every one selectable for every kind of action. A refused one stays on offer, with the server's sentence beside it. */
export function LevelChoice({ effect, value, onChange, refused, cautious }: Props) {
  const name = useId();
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-xs font-medium text-text">How far the agent may go</legend>
      {LEVELS.map((level) => {
        const sentence = refused[level];
        const selected = value === level;
        const calm = level === "alone" ? cautiousAloneNote(effect, cautious) : null;
        const id = `${name}-${level}`;
        return (
          <div key={level} className={`rounded-lg border px-3 py-2.5 ${selected ? "border-accent bg-surface-2" : "border-border"}`}>
            <label htmlFor={id} className="flex cursor-pointer items-start gap-2.5">
              <input id={id} type="radio" name={name} className="mt-0.5" checked={selected} onChange={() => onChange(level)} aria-describedby={`${id}-about${sentence ? ` ${id}-refusal` : ""}`} />
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium text-text">{LEVEL_LABELS[level]}</span>
                <span id={`${id}-about`} className="block text-xs text-text-muted">
                  {levelExplanation(level, effect)}
                  {calm ? <span className="mt-1 block">{calm}</span> : null}
                </span>
              </span>
            </label>
            {sentence ? (
              <p id={`${id}-refusal`} className="mt-1.5 break-words pl-[1.65rem] text-xs text-warning" role={selected ? "alert" : undefined}>
                {sentence}
              </p>
            ) : null}
          </div>
        );
      })}
    </fieldset>
  );
}
