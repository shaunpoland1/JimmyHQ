// Which cached integrations data a live Tape event makes stale.

export type HandsData = "providers" | "accounts" | "rules" | "runs" | "actions";

export function staleAfter(kind: string): HandsData[] {
  switch (kind) {
    case "hands.provider_saved":
    case "hands.provider_removed":
      return ["providers", "accounts", "rules", "actions"];
    case "hands.account_connected":
    case "hands.account_forgotten":
      return ["accounts", "rules", "actions"];
    case "hands.rule_set":
    case "hands.rule_removed":
      return ["rules"];
    case "hand.called":
    case "hand.proposed":
      // A call counts toward today's number on its rule.
      return ["runs", "rules"];
    default:
      return [];
  }
}
