// Mail inboxes made for agents (AgentMail): how the connect form asks for one, and how an
// account's own address is shown. The levels for who mail goes to, and how mail that says it
// is from you is treated, are ordinary settings under Settings, Integrations.

import type { Account } from "../api.ts";

/** Provider types whose accounts are inboxes made for one agent each. */
export const INBOX_TYPES: readonly string[] = ["agentmail"];

/** The part before the @: mirrors the server's check. */
export const INBOX_NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/;

export function makesInboxes(type: string | null | undefined): boolean {
  return !!type && INBOX_TYPES.includes(type);
}

export type ConnectShape = {
  /** The label of the field sent as `app`. */
  appLabel: string;
  appHint: string;
  /** Shown with the name and short name, not tucked under Advanced. */
  appInSight: boolean;
  /** The account is an agent's own inbox, always: the box is not offered. */
  ownInboxFixed: boolean;
  intro: string | null;
};

/** How the connect form asks for an account of this type of provider. */
export function connectShape(type: string | null | undefined): ConnectShape {
  if (makesInboxes(type)) {
    return {
      appLabel: "Inbox name (optional)",
      appHint: "The part of the new address before the @, for example ada. Empty lets the provider choose.",
      appInSight: true,
      ownInboxFixed: true,
      intro: "Connecting makes a new inbox with its own address. Connect one for each agent that should have one, then switch on what that agent may do with it.",
    };
  }
  return {
    appLabel: "App (optional)",
    appHint: "Only when the provider hosts several apps and you want one of them.",
    appInSight: false,
    ownInboxFixed: false,
    intro: null,
  };
}

/** What is wrong with an inbox name, or null. Empty is allowed. */
export function inboxNameProblem(name: string): string | null {
  const value = name.trim().toLowerCase();
  if (!value) return null;
  if (!INBOX_NAME.test(value)) return "An inbox name uses only lower-case letters, digits, dots, - and _ (64 at most), and starts with a letter or digit.";
  return null;
}

/** The line under an account that has an address of its own, or null. */
export function addressLine(account: Pick<Account, "address" | "own_inbox">): string | null {
  const address = account.address?.trim();
  if (!address) return null;
  return account.own_inbox ? `Agent's address: ${address}` : `Address: ${address}`;
}
