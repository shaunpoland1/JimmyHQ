import { Field } from "../../components/Field";
import { EFFECTS, RECIPIENT_LIMITS } from "./api";
import type { ActionInfo, Effect, Level, RecipientLimit } from "./api";
import { ArgumentsEditor } from "./ArgumentsEditor";
import { LevelChoice } from "./LevelChoice";
import { TryPanel } from "./TryPanel";
import { argumentRows, canTry } from "./lib/args";
import { hasErrors } from "./lib/rule";
import type { DraftErrors, RuleDraft } from "./lib/rule";
import { CAUTIOUS, EFFECT_LABELS, effectSentence, isGuess, sourceLabel, sourceSentence } from "./lib/wording";
import { AreaField, Notice, SelectField, Tag } from "./ui";

const WHO: Record<RecipientLimit, string> = { owner: "Only to me", known: "Only to people I know", anyone: "To anyone" };

type Props = {
  agentName: string;
  account: string;
  action: ActionInfo;
  draft: RuleDraft;
  errors: DraftErrors;
  /** The server's sentence for a level, by level. */
  refused: Partial<Record<Level, string>>;
  /** Why a reading did not start at "Alone", in the server's words, or null. */
  levelNote: string | null;
  /** Whether the owner's list is careful with this kind of action, as the server says. */
  cautious?: boolean;
  onChange: (next: RuleDraft) => void;
};

/** Everything about one rule: what the action does, how far the agent may go, its arguments, what it is shown and its limits. */
export function RuleForm({ agentName, account, action, draft, errors, refused, levelNote, cautious, onChange }: Props) {
  const rows = argumentRows(action.arguments);
  const set = (patch: Partial<RuleDraft>) => onChange({ ...draft, ...patch });
  const corrected = draft.effect !== action.effect;
  const loosened = corrected && draft.effect === "read";
  const effectNow = draft.effect;

  return (
    <div className="flex flex-col gap-5">
      <section aria-labelledby="rule-what" className="flex flex-col gap-2">
        <h3 id="rule-what" className="text-[13px] font-semibold text-text">
          What it does
        </h3>
        <p className="break-words text-sm text-text">{action.description || action.label}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Tag tone={CAUTIOUS.includes(effectNow) ? "warn" : undefined}>{EFFECT_LABELS[effectNow]}</Tag>
          <span className="text-xs text-text-muted">{corrected ? "as you set it" : sourceLabel(action.effect_from)}</span>
        </div>
        <p className="text-xs text-text-muted">
          {effectSentence(effectNow)} {corrected ? "You set this yourself." : sourceSentence(action.effect_from)}
        </p>
        {isGuess(action.effect_from) && !corrected ? <Notice tone="warn">Nobody is sure what this action does. Check it before you go further, and correct it below if it is wrong.</Notice> : null}
        <SelectField
          label="What kind of action is this?"
          value={draft.effect}
          onChange={(event) => set({ effect: event.target.value as Effect })}
          hint={`The provider's own answer is "${EFFECT_LABELS[action.effect]}". Correct it if it is wrong: it decides which level it starts at.`}
        >
          {EFFECTS.map((effect) => (
            <option key={effect} value={effect}>
              {EFFECT_LABELS[effect]}
              {effect === action.effect ? " (as reported)" : ""}
            </option>
          ))}
        </SelectField>
        {loosened ? <Notice tone="warn">You are marking an action as one that only reads. A reading can run alone, with no question asked. Only do this if you are sure it changes nothing.</Notice> : null}
      </section>

      <section aria-labelledby="rule-level" className="flex flex-col gap-2">
        <h3 id="rule-level" className="text-[13px] font-semibold text-text">
          Level
        </h3>
        {levelNote ? <Notice tone="warn">{levelNote}</Notice> : null}
        <LevelChoice effect={effectNow} value={draft.level} onChange={(level) => set({ level })} refused={refused} cautious={cautious} />
      </section>

      <section aria-labelledby="rule-args" className="flex flex-col gap-2">
        <h3 id="rule-args" className="text-[13px] font-semibold text-text">
          Arguments
        </h3>
        <p className="text-xs text-text-muted">What goes into each request. A value you fix is never shown to {agentName}, and {agentName} cannot change it.</p>
        <ArgumentsEditor rows={rows} value={draft.args} errors={errors.args} onChange={(args) => set({ args })} />
      </section>

      <section aria-labelledby="rule-sees" className="flex flex-col gap-3">
        <h3 id="rule-sees" className="text-[13px] font-semibold text-text">
          What {agentName} is shown of the answer
        </h3>
        <label className="flex items-start gap-2 text-xs text-text">
          <input type="checkbox" className="mt-0.5" checked={draft.showAddresses} onChange={(event) => set({ showAddresses: event.target.checked })} />
          <span>Show email addresses and phone numbers. Off, they are hidden before {agentName} sees the answer. Secrets are always taken out.</span>
        </label>
        <AreaField
          label="Only these fields (optional)"
          value={draft.fields}
          onChange={(event) => set({ fields: event.target.value })}
          error={errors.fields}
          hint="One field name on each line. Empty shows everything the provider sent that is not a secret."
          rows={2}
          spellCheck={false}
        />
      </section>

      <section aria-labelledby="rule-limits" className="flex flex-col gap-3">
        <h3 id="rule-limits" className="text-[13px] font-semibold text-text">
          Limits
        </h3>
        <p className="text-xs text-text-muted">At Alone, a call past a limit waits for your yes on the Bench instead of running. Leave a box empty for no limit.</p>
        <div className="grid grid-cols-1 gap-3 @md:grid-cols-3">
          <Field label="Times a day" value={draft.perDay} onChange={(event) => set({ perDay: event.target.value })} error={errors.perDay} inputMode="numeric" autoComplete="off" hint="A whole number." />
          {action.limits_offered.recipients ? (
            <SelectField label="Who may receive it" value={draft.recipients} onChange={(event) => set({ recipients: event.target.value as RecipientLimit | "" })} error={errors.recipients} hint="Your own and known addresses are set under Settings, Integrations.">
              <option value="">No limit</option>
              {RECIPIENT_LIMITS.map((who) => (
                <option key={who} value={who}>
                  {WHO[who]}
                </option>
              ))}
            </SelectField>
          ) : null}
          {action.limits_offered.max_amount ? (
            <Field label={`Largest ${action.amount ?? "amount"}`} value={draft.maxAmount} onChange={(event) => set({ maxAmount: event.target.value })} error={errors.maxAmount} inputMode="decimal" autoComplete="off" hint="0 or more." />
          ) : null}
        </div>
        {!action.limits_offered.recipients && !action.limits_offered.max_amount ? <p className="text-xs text-text-faint">This action does not say who receives it or carry an amount, so only a daily number can limit it.</p> : null}
      </section>

      {canTry(action) ? <TryPanel account={account} action={action.name} rows={rows} /> : null}

      {hasErrors(errors) ? <Notice tone="bad">Some of the choices above need fixing before this can be saved.</Notice> : null}
    </div>
  );
}
