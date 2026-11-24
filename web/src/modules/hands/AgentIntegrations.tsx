import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plug, Plus, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { errorDetail } from "../../api";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { EmptyState } from "../../components/EmptyState";
import { useToast } from "../../hooks/useToast";
import { formatHash } from "../../lib/route";
import { handsApi } from "./api";
import type { Rule } from "./api";
import { RuleDialog } from "./RuleDialog";
import { RunsList } from "./RunsList";
import { countOn, groupByAccount, needReview, usableAccounts } from "./lib/group";
import { slotsLeft } from "./lib/rule";
import { EFFECT_LABELS, levelShort, limitsLine, ruleStateAdvice, ruleStateLine, suggestionLine } from "./lib/wording";
import { HANDS_KEYS, useAccountsQuery, useHandsLive, useProvidersQuery, useRulesQuery } from "./queries";
import { CARD, Failed, Loading, Notice, Pill, SMALL_BTN, Tag } from "./ui";

type Props = { agent: string; agentName: string; retired: boolean };

/** The Integrations tab of one agent: the actions it may use, grouped by account, how far it may go with each, and what it did lately. */
export function AgentIntegrations({ agent, agentName, retired }: Props) {
  useHandsLive();
  const client = useQueryClient();
  const toast = useToast();
  const rules = useRulesQuery(agent);
  const accounts = useAccountsQuery();
  const providers = useProvidersQuery();
  const [editing, setEditing] = useState<{ rule: Rule } | { add: true } | null>(null);
  const [removing, setRemoving] = useState<Rule | null>(null);

  const remove = useMutation({
    mutationFn: (rule: Rule) => handsApi.removeRule(rule.agent, rule.account, rule.action),
    onSuccess: (_result, rule) => toast.success(`${rule.label} is switched off for ${agentName}.`),
    onError: (error) => toast.error(errorDetail(error)),
    onSettled: () => {
      setRemoving(null);
      void client.invalidateQueries({ queryKey: HANDS_KEYS.rules(agent) });
    },
  });

  const list = rules.data?.rules ?? [];
  const most = rules.data?.max_tools_per_agent;
  const usable = usableAccounts(accounts.data?.accounts, providers.data?.providers);
  const groups = groupByAccount(list, accounts.data?.accounts);
  const review = needReview(list);
  const left = slotsLeft(list.length, most);
  const settingsLink = formatHash({ screen: "settings", sub: "hands" });
  const noAccounts = accounts.isSuccess && (accounts.data?.accounts.length ?? 0) === 0;

  return (
    <div className="@container flex min-w-0 flex-col gap-5">
      <section aria-labelledby="hands-rules-heading" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="hands-rules-heading" className="text-[13px] font-semibold text-text">
            What {agentName} may use
          </h2>
          {rules.isSuccess ? (
            <span className="text-xs text-text-faint">
              {countOn(list)} on{typeof most === "number" ? `, room for ${left} more (at most ${most})` : ""}
            </span>
          ) : null}
          <div className="ml-auto">
            <button type="button" className="btn btn-primary h-8" onClick={() => setEditing({ add: true })} disabled={retired || usable.length === 0 || !rules.isSuccess}>
              <Plus size={14} aria-hidden="true" />
              Switch on an action
            </button>
          </div>
        </div>
        <p className="max-w-[66ch] text-xs text-text-muted">
          {agentName} can use nothing outside until you switch an action on, one at a time. Each one has a level: read only, ask you each time, or alone. You choose it, and can change it whenever you like. Accounts and providers are set up under{" "}
          <a className="text-accent underline underline-offset-2" href={settingsLink}>
            Settings, Integrations
          </a>
          .
        </p>

        {review.length > 0 ? (
          <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-surface-2 px-3 py-2.5 text-xs text-text" role="status">
            <TriangleAlert size={14} className="mt-0.5 flex-none text-warning" aria-hidden="true" />
            <p>
              {review.length === 1 ? "1 action changed" : `${review.length} actions changed`} at {review.length === 1 ? "its" : "their"} provider after you switched {review.length === 1 ? "it" : "them"} on. {agentName} is not offered {review.length === 1 ? "it" : "them"} until you have reviewed {review.length === 1 ? "it" : "them"}: open{" "}
              {review.length === 1 ? "it" : "each"} and save {review.length === 1 ? "it" : "them"} again.
            </p>
          </div>
        ) : null}

        {rules.isPending || accounts.isPending ? <Loading>Loading {agentName}'s integrations…</Loading> : null}
        {rules.isError ? <Failed what="This agent's integrations" error={rules.error} onRetry={() => void rules.refetch()} /> : null}
        {accounts.isError ? <Failed what="The list of accounts" error={accounts.error} onRetry={() => void accounts.refetch()} /> : null}

        {rules.isSuccess && list.length === 0 ? (
          <div className={CARD}>
            {noAccounts ? (
              <EmptyState icon={Plug} title="No account is connected" description={`${agentName} has nothing to use yet. Add a provider and connect an account under Settings, Integrations, then come back to switch actions on.`} compact>
                <a className="btn btn-primary no-underline" href={settingsLink}>
                  Open Settings, Integrations
                </a>
              </EmptyState>
            ) : (
              <EmptyState icon={Plug} title="Nothing switched on" description={`${agentName} cannot use any integration yet. Switch on the first action to start, such as one that only reads.`} compact>
                {usable.length > 0 && !retired ? (
                  <button type="button" className="btn btn-primary" onClick={() => setEditing({ add: true })}>
                    Switch on an action
                  </button>
                ) : null}
              </EmptyState>
            )}
          </div>
        ) : null}

        {groups.map((group) => (
          <section key={group.account} className={`${CARD} min-w-0`} aria-label={`Actions on ${group.label}`}>
            <h3 className="flex flex-wrap items-baseline gap-x-2 border-b border-border px-3 py-2 text-[13px] font-semibold text-text">
              {group.label}
              <span className="font-mono text-[11px] font-normal text-text-faint">{group.account}</span>
              {!group.present ? <span className="text-xs font-normal text-danger">not connected</span> : null}
            </h3>
            <ul>
              {group.rules.map((rule) => (
                <RuleRow key={rule.action} rule={rule} agentName={agentName} onEdit={() => setEditing({ rule })} onRemove={() => setRemoving(rule)} />
              ))}
            </ul>
          </section>
        ))}
      </section>

      <RunsList agent={agent} agentName={agentName} rules={list} />

      {editing ? (
        <RuleDialog
          agent={agent}
          agentName={agentName}
          accounts={usable}
          rules={list}
          maxTools={most}
          rule={"rule" in editing ? editing.rule : undefined}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {removing ? (
        <ConfirmDialog
          title={`Switch off ${removing.label}?`}
          message={`${agentName} is no longer offered it. Requests it already put on the Bench for your yes can no longer be approved. You can switch it on again later, and its settings are not kept.`}
          confirmLabel="Switch off"
          danger
          busy={remove.isPending}
          onConfirm={() => remove.mutate(removing)}
          onCancel={() => setRemoving(null)}
        />
      ) : null}
    </div>
  );
}

function RuleRow({ rule, agentName, onEdit, onRemove }: { rule: Rule; agentName: string; onEdit: () => void; onRemove: () => void }) {
  const state = ruleStateLine(rule.state);
  const advice = ruleStateAdvice(rule);
  const limits = limitsLine(rule);
  const todayText = rule.limits?.per_day != null ? `${rule.today} of ${rule.limits.per_day} used today` : rule.today > 0 ? `${rule.today} ${rule.today === 1 ? "use" : "uses"} today` : null;
  const offered = rule.state === "on" && (rule.effect === "read" || rule.level !== "read");
  const suggestion = rule.state === "on" ? suggestionLine(rule, agentName) : null;
  return (
    <li className="flex flex-col gap-1 border-b border-border/55 px-3 py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <span className="min-w-0 break-words text-[13px] font-medium text-text">{rule.label}</span>
        <Tag title={rule.effect_from === "owner" ? "You set this" : rule.effect_from === "guess" ? "A guess" : undefined} tone={rule.effect_from === "guess" ? "warn" : undefined}>
          {EFFECT_LABELS[rule.effect] ?? rule.effect}
        </Tag>
        <Pill tone={rule.level === "alone" ? "warn" : "none"}>{levelShort(rule.level)}</Pill>
        {rule.state !== "on" ? <Pill tone={state.tone}>{state.text}</Pill> : !offered ? <Pill tone="none">not offered</Pill> : null}
        <span className="ml-auto flex items-center gap-1.5">
          <button type="button" className={SMALL_BTN} onClick={onEdit} aria-label={`${rule.state === "changed" ? "Review" : "Edit"} ${rule.label} for ${agentName}`}>
            {rule.state === "changed" ? "Review" : "Edit"}
          </button>
          <button type="button" className={SMALL_BTN} onClick={onRemove} aria-label={`Switch off ${rule.label} for ${agentName}`}>
            Switch off
          </button>
        </span>
      </div>
      {limits || todayText ? (
        <p className="text-xs text-text-faint">
          {[limits ? `Limits: ${limits}` : null, todayText].filter(Boolean).join(" · ")}
        </p>
      ) : null}
      {advice ? <Notice tone={rule.state === "changed" ? "warn" : undefined}>{advice}</Notice> : null}
      {suggestion ? <p className="text-xs text-text-muted">{suggestion}</p> : null}
      {rule.state === "on" && !offered ? <p className="text-xs text-text-faint">It changes things and is set to read only, so {agentName} is not offered it.</p> : null}
    </li>
  );
}
