// Settings sections that need more than a list of simple values.
//
// Most settings are generated from the server's schema. A module whose
// settings are richer (a list of providers, a table of roles) adds its own
// section here: an id for the URL, a label, and the component to render.

import type { ComponentType } from "react";
import { SettingsFile } from "../components/SettingsFile";
import { useSettingsQuery } from "../hooks/useSettings";
import { ApiTokensExtra } from "../modules/access/ApiTokensExtra";
import { OwnerAccessExtra } from "../modules/access/OwnerAccessExtra";
import { appSettingsExtra } from "../modules/apps/AppSecrets";
import { BackupSettingsExtra } from "../modules/backup/BackupSettingsExtra";
import { DoorSettingsExtra } from "../modules/door/DoorSettingsExtra";
import { HandsSettings } from "../modules/hands/HandsSettings";
import { LookupSettingsExtra } from "../modules/lookup/LookupSettingsExtra";
import { ModelsSettings } from "../modules/models/ModelsSettings";
import { NodesSettingsExtra } from "../modules/nodes/NodesSettingsExtra";
import { RemoteSettingsExtra } from "../modules/remote/RemoteSettingsExtra";
import { SpeechSettingsExtra } from "../modules/speech/SpeechSettingsExtra";

export type CustomSection = {
  id: string;
  label: string;
  component: ComponentType;
  /**
   * Words the Settings search finds this page by, besides its name and its
   * description. What is on the page is not generated from the schema, so the
   * search cannot read it: name here what a person would type to get to it.
   */
  keywords: readonly string[];
};

/** The Import and export page. Its place in the list comes from the server, like any section's. */
function SettingsFilePage() {
  const query = useSettingsQuery();
  return <SettingsFile settings={query.data?.settings ?? []} />;
}

// Where a section is listed is the server's decision (its group and order);
// the label here wins over the server's when both name the section.
export const CUSTOM_SECTIONS: readonly CustomSection[] = [
  {
    id: "models",
    label: "Models and roles",
    component: ModelsSettings,
    keywords: ["providers", "API key", "local model", "hosted model", "default model", "embeddings", "prices", "price table", "usage", "Ollama", "OpenAI", "Anthropic", "OpenRouter"],
  },
  {
    id: "hands",
    label: "Integrations",
    component: HandsSettings,
    keywords: ["accounts", "connect an account", "providers", "actions", "tools", "MCP servers", "Composio", "email", "calendar", "approvals"],
  },
  {
    id: "file",
    label: "Import and export",
    component: SettingsFilePage,
    keywords: ["backup", "restore", "settings file", "profile", "share settings", "download", "upload", "move to another computer"],
  },
];

/** Settings, Access has two modules' controls: the owner's own password, sessions and API tokens, then reaching JimmyHQ from other devices. */
function AccessExtras() {
  return (
    <div className="flex flex-col gap-3">
      <OwnerAccessExtra />
      <ApiTokensExtra />
      <RemoteSettingsExtra />
    </div>
  );
}

/**
 * Extra controls shown above a generated section's settings, keyed by the
 * section id the server uses. For things a simple value cannot express, such
 * as storing an API key or showing whether an engine is ready.
 */
export const SECTION_EXTRAS: Readonly<Record<string, ComponentType>> = {
  voice: SpeechSettingsExtra,
  lookup: LookupSettingsExtra,
  door: DoorSettingsExtra,
  access: AccessExtras,
  nodes: NodesSettingsExtra,
  backup: BackupSettingsExtra,
};

/**
 * The extra controls for a page: a fixed one from `SECTION_EXTRAS`, or for an installed
 * app's own page (`app.<id>`) the fields for the secrets it declares.
 */
export function sectionExtra(sectionId: string): ComponentType | undefined {
  return SECTION_EXTRAS[sectionId] ?? appSettingsExtra(sectionId);
}

/**
 * Words the Settings search finds a page by for what its extra controls hold,
 * keyed like `SECTION_EXTRAS`. The page's generated settings are found by
 * their own words; these cover the part drawn by hand.
 */
export const EXTRA_KEYWORDS: Readonly<Record<string, readonly string[]>> = {
  voice: ["dictation", "microphone", "speech API key", "Whisper"],
  lookup: ["search API key", "Brave", "Tavily", "SearXNG", "internet", "browse"],
  door: ["MCP", "Model Context Protocol", "MCP door", "clients", "tokens", "connector", "assistant", "outside systems", "recent calls"],
  access: ["Tailscale", "remote access", "phone", "other devices", "private network", "password", "change password", "forgot password", "sign out everywhere", "other browsers", "sessions", "API token", "tokens", "revoke token", "script", "bearer"],
  backup: ["back up now", "restore", "restore test", "snapshot", "copy of my data", "second drive"],
};

/** Every page's search keywords by section id: the custom pages' and the extras'. */
export const SEARCH_KEYWORDS: Readonly<Record<string, readonly string[]>> = {
  ...EXTRA_KEYWORDS,
  ...Object.fromEntries(CUSTOM_SECTIONS.map((section) => [section.id, section.keywords])),
};
