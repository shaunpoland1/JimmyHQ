import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { ApiError, errorDetail } from "../../api";
import { Modal } from "../../components/Modal";
import { useToast } from "../../hooks/useToast";
import { handsApi } from "./api";
import type { Account, ActionInfo, Level, PreflightBody, Rule } from "./api";
import { ActionPicker } from "./ActionPicker";
import { RuleForm } from "./RuleForm";
import { alreadyOn, findAction } from "./lib/group";
import { noteRefusal, remembered } from "./lib/refusals";
import type { RefusalMemory } from "./lib/refusals";
import { draftFromRule, hasErrors, newDraft, preflightBody, refusedLevels, ruleBody, sameAsSaved, slotsLeft, validateDraft } from "./lib/rule";
import type { RuleDraft } from "./lib/rule";
import { LEVEL_LABELS, ruleStateAdvice } from "./lib/wording";
import { HANDS_KEYS, useActionsQuery, usePreflightQuery } from "./queries";
import { Notice, SelectField } from "./ui";

// What the server said about a level, kept for the session so the next form says it too.
const MEMORY: RefusalMemory = new Map();

type Props = {
  agent: string;
  agentName: string;
  accounts: Account[];
  rules: Rule[];
  maxTools: number | undefined;
  /** An existing rule to look at and change; without it, a new one is made. */
  rule?: Rule;
  onClose: () => void;
};

/** Switch one action on for one agent, or change how: pick the account and action, then set the rule. */
export function RuleDialog({ agent, agentName, accounts, rules, maxTools, rule, onClose }: Props) {
  const client = useQueryClient();
  const toast = useToast();
  const [account, setAccount] = useState(rule?.account ?? accounts[0]?.account ?? "");
  const [chosen, setChosen] = useState<string | null>(rule?.action ?? null);
  const [draft, setDraft] = useState<RuleDraft | null>(null);
  const [draftFor, setDraftFor] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [, bump] = useState(0);
  const body = useRef<HTMLDivElement>(null);

  const actions = useActionsQuery(account);
  const action: ActionInfo | undefined = findAction(actions.data?.actions, chosen ?? "");
  const held = accounts.find((a) => a.account === account);
  const editing = rule !== undefined;
  const taken = useMemo(() => (editing ? new Set<string>() : alreadyOn(rules, account)), [editing, rules, account]);
  const slots = slotsLeft(rules.length, maxTools);

  // The form belongs to one action; choosing another starts it again from the server's defaults.
  const key = action ? `${account}/${action.name}/${action.version}` : null;
  if (action && key !== draftFor) {
    setDraftFor(key);
    setDraft(rule && rule.action === action.name ? draftFromRule(rule, action) : newDraft(action, held?.own_inbox ?? false));
    setProblem(null);
    setShowErrors(false);
  }
  if (!action && draft !== null) {
    setDraft(null);
    setDraftFor(null);
  }

  const errors = action && draft ? validateDraft(action, draft) : {};
  // The server is asked which levels this rule may take before anything is saved, so the
  // reason stands next to the choice. What an earlier save was told is the fallback.
  const question: PreflightBody | null = action && draft ? preflightBody(agent, account, action, draft, errors) : null;
  const preflight = usePreflightQuery(question);
  const refused: Partial<Record<Level, string>> = refusedLevels(preflight.data);
  if (action && draft && !preflight.data) {
    for (const level of ["read", "ask", "alone"] as const) {
      const said = remembered(MEMORY, draft.effect, level);
      if (said) refused[level] = said;
    }
  }
  // The server says whether the owner's own list is careful with this kind of action.
  const cautious = action && draft ? (preflight.data ? preflight.data.cautious : draft.effect === action.effect ? action.cautious : undefined) : undefined;
  const levelNote = action && draft ? (preflight.data ? preflight.data.level_note : draft.effect === action.effect ? action.level_note : null) : null;
  const unchanged = rule && action && draft ? sameAsSaved(rule, action, draft) && rule.state === "on" : false;
  const full = !editing && slots !== null && slots <= 0;

  const [refreshing, setRefreshing] = useState(false);
  const askAgain = async () => {
    setRefreshing(true);
    try {
      client.setQueryData(HANDS_KEYS.actions(account), await handsApi.actions(account, true));
    } catch (error) {
      toast.error(errorDetail(error));
    } finally {
      setRefreshing(false);
    }
  };

  const save = async () => {
    if (!action || !draft || busy) return;
    setShowErrors(true);
    setProblem(null);
    if (hasErrors(errors)) {
      body.current?.querySelector<HTMLElement>('[aria-invalid="true"], [role="alert"]')?.scrollIntoView?.({ block: "center" });
      return;
    }
    const request = ruleBody(agent, account, action, draft);
    if (!request) return;
    setBusy(true);
    try {
      await handsApi.setRule(request);
      void client.invalidateQueries({ queryKey: HANDS_KEYS.rules(agent) });
      void client.invalidateQueries({ queryKey: HANDS_KEYS.actions(account) });
      toast.success(`${action.label} is ${editing ? "saved" : "switched on"} for ${agentName} at "${LEVEL_LABELS[draft.level]}".`);
      onClose();
    } catch (error) {
      const sentence = errorDetail(error);
      // A refusal of the level is said next to the choice; anything else at the foot of the form.
      const aboutLevel = error instanceof ApiError && error.status === 422 && noteRefusal(MEMORY, draft.effect, draft.level, sentence);
      // Beside the level when the form shows it there; otherwise at the foot, so it is never lost.
      const besideLevel = aboutLevel && (!preflight.data || !preflight.data.levels[draft.level]?.allowed);
      if (besideLevel) bump((n) => n + 1);
      else setProblem(sentence);
      void client.invalidateQueries({ queryKey: HANDS_KEYS.preflight(question) });
      void client.invalidateQueries({ queryKey: HANDS_KEYS.actions(account) });
    } finally {
      setBusy(false);
    }
  };

  const title = editing ? `${rule.label} for ${agentName}` : `Switch on an action for ${agentName}`;

  return (
    <Modal title={title} onClose={onClose} panelClass="max-h-[90vh] w-full max-w-2xl">
      <form
        className="flex min-h-0 flex-1 flex-col"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        noValidate
      >
        <div ref={body} className="@container flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-4 py-4">
          {editing && rule.state !== "on" ? <Notice tone="warn">{ruleStateAdvice(rule)}</Notice> : null}
          {editing && rule.state === "changed" ? <Notice tone="warn">Read what the action does now, below. Saving puts the rule back on with the new definition, as you have set it here.</Notice> : null}

          {!editing ? (
            <>
              {accounts.length > 1 ? (
                <SelectField
                  label="Account"
                  value={account}
                  onChange={(event) => {
                    setAccount(event.target.value);
                    setChosen(null);
                  }}
                >
                  {accounts.map((item) => (
                    <option key={item.account} value={item.account}>
                      {item.label} ({item.account})
                    </option>
                  ))}
                </SelectField>
              ) : (
                <p className="text-xs text-text-muted">
                  Account: <span className="font-medium text-text">{held?.label ?? account}</span> <span className="font-mono text-text-faint">{account}</span>
                </p>
              )}
              {full ? <Notice tone="warn">{agentName} already holds {maxTools} integration actions, which is the most allowed. Switch one off first, or raise the number under Settings, Integrations.</Notice> : null}
              <div className="flex flex-col gap-1.5">
                <h3 className="text-[13px] font-semibold text-text">Action</h3>
                <ActionPicker actions={actions.data?.actions} loading={actions.isPending} error={actions.isError ? actions.error : null} onRetry={() => void actions.refetch()} taken={taken} chosen={chosen} onChoose={setChosen} />
                <button type="button" className="self-start rounded text-xs text-accent hover:underline disabled:opacity-60" onClick={() => void askAgain()} disabled={refreshing}>
                  {refreshing ? "Asking the provider…" : "Not there? Ask the provider again"}
                </button>
              </div>
            </>
          ) : actions.isPending ? (
            <p className="text-sm text-text-faint" role="status">
              Asking the provider what this action does now…
            </p>
          ) : actions.isError ? (
            <Notice tone="bad">{errorDetail(actions.error)} The rule below cannot be changed until the provider answers.</Notice>
          ) : !action ? (
            <Notice tone="warn">The provider no longer offers this action, so it cannot be changed. Remove it.</Notice>
          ) : null}

          {action && draft ? (
            <>
              {!editing ? <hr className="border-border" /> : null}
              <RuleForm agentName={agentName} account={account} action={action} draft={draft} errors={showErrors ? errors : {}} refused={refused} levelNote={levelNote} cautious={cautious} onChange={setDraft} />
            </>
          ) : !editing && !actions.isPending && !actions.isError ? (
            <p className="text-xs text-text-faint">Choose an action to set how {agentName} may use it.</p>
          ) : null}

          {problem ? <Notice tone="bad">{problem}</Notice> : null}
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !action || !draft || full || unchanged}>
            {busy ? "Saving…" : editing ? "Save" : "Switch on"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
