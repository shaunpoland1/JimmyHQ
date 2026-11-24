import type { ActionInfo, ProviderType, RecordField, Rule } from "../src/modules/hands/api.ts";

/** One field of a provider type's record. */
export function field(over: Partial<RecordField> & Pick<RecordField, "name">): RecordField {
  return { label: over.name, kind: "text", help: "", required: false, where: "options", secret_prefix: null, only_for: null, ...over };
}

/** A provider type as GET /types describes it. */
export function providerType(over: Partial<ProviderType> = {}): ProviderType {
  return { type: "mailbox", label: "Mailbox", needs_key: true, needs_address: false, note: "", description: "", fields: [], tokens: "none", tokens_note: "", trusted_hints: false, ...over };
}

/** A tool server: a program on this computer or an address, as the server declares the type. */
export function serverType(): ProviderType {
  return providerType({
    type: "mcp",
    label: "MCP server",
    needs_key: false,
    tokens: "here",
    fields: [
      field({ name: "base_url", label: "Address", where: "record", only_for: "address", required: true }),
      field({ name: "command", label: "Program", kind: "path", only_for: "program", required: true }),
      field({ name: "args", label: "Arguments", kind: "argv", only_for: "program" }),
      field({ name: "env", label: "Environment variables", kind: "names", secret_prefix: "env_", only_for: "program" }),
      field({ name: "headers", label: "Headers", kind: "names", secret_prefix: "header_", only_for: "address" }),
      field({ name: "max_tools", label: "Most tools", kind: "number" }),
    ],
  });
}

/** An action as the server describes it, with sensible defaults a test can override. */
export function action(over: Partial<ActionInfo> = {}): ActionInfo {
  return {
    name: "messages.send",
    label: "Send a message",
    description: "Sends one message.",
    arguments: {
      type: "object",
      properties: {
        to: { type: "string", description: "Who gets it" },
        subject: { type: "string" },
        text: { type: "string" },
        priority: { enum: ["low", "high"] },
        copies: { type: "array", items: { type: "string" } },
        attachments: { type: "array", items: { type: "object" } },
      },
      required: ["to", "text"],
    },
    effect: "send",
    effect_from: "provider",
    recipients: ["to"],
    amount: null,
    undo: false,
    version: "v1",
    tool: "mail_messages_send",
    can_offer: true,
    cannot_offer_reason: null,
    default_arguments: {},
    default_level: "ask",
    effect_trusted: false,
    level_note: null,
    limits_offered: { per_day: true, recipients: true, max_amount: false },
    ...over,
  };
}

export function rule(over: Partial<Rule> = {}): Rule {
  return {
    agent: "ada",
    account: "mail",
    action: "messages.send",
    tool: "mail_messages_send",
    label: "Send a message",
    level: "ask",
    effect: "send",
    effect_from: "provider",
    version: "v1",
    arguments: {
      attachments: { mode: "hidden", value: null },
      copies: { mode: "hidden", value: null },
      priority: { mode: "hidden", value: null },
      subject: { mode: "hidden", value: null },
      text: { mode: "open", value: null },
      to: { mode: "open", value: null },
    },
    show_addresses: false,
    fields: [],
    limits: { per_day: null, recipients: null, max_amount: null },
    confirmed: false,
    state: "on",
    reason: null,
    today: 0,
    ...over,
  };
}
