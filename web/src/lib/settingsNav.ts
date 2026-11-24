// Where each settings section sits on the Settings screen: the groups in the
// left-hand list, the pages in each group, the headed parts of a long page,
// and what a link such as `#/settings/memory/memory.ignore_below` points at.
//
// The server decides the order and the grouping (see `section()` in
// `server/jimmyhq/settings.py`). Everything here only reads that.

import type { Setting, SettingsLayout, SettingsSection } from "../api.ts";
import { formatHash } from "./route.ts";

export type NavPart = { id: string; label: string; description: string };
export type NavPage = NavPart & { parts: NavPart[] };
export type NavGroup = { id: string; label: string; pages: NavPage[] };

/** The group for a section that names none, so it is still listed. */
export const FALLBACK_GROUP = { id: "more", label: "More" } as const;

/** The layout to use when the server sends none (an older server): every section is its own page. */
export const NO_LAYOUT: SettingsLayout = { groups: [], sections: {} };

/**
 * The left-hand list: groups, each with its pages, each page with its parts.
 * `sections` is already in the order to show. `custom` are sections the web
 * app draws itself: one the server also lists takes the custom label, and one
 * it does not list is added under "More" so it can still be reached.
 */
export function buildNav(sections: readonly SettingsSection[], layout: SettingsLayout, custom: readonly { id: string; label: string }[] = []): NavGroup[] {
  const customLabel = new Map(custom.map((section) => [section.id, section.label]));
  const listed = new Set(sections.map((section) => section.id));
  const all = [...sections, ...custom.filter((section) => !listed.has(section.id))];
  const ids = new Set(all.map((section) => section.id));

  const groups = new Map<string, NavGroup>(layout.groups.map((group) => [group.id, { ...group, pages: [] }]));
  const pages = new Map<string, NavPage>();
  const parts: { parent: string; part: NavPart }[] = [];

  for (const section of all) {
    const place = layout.sections[section.id];
    const entry: NavPart = { id: section.id, label: customLabel.get(section.id) ?? section.label, description: place?.description ?? "" };
    // A part whose page is missing becomes a page, so its settings stay reachable.
    if (place?.parent && place.parent !== section.id && ids.has(place.parent) && !layout.sections[place.parent]?.parent) {
      parts.push({ parent: place.parent, part: entry });
      continue;
    }
    const page: NavPage = { ...entry, parts: [] };
    pages.set(page.id, page);
    let group = groups.get(place?.group ?? FALLBACK_GROUP.id) ?? groups.get(FALLBACK_GROUP.id);
    if (!group) {
      // "More" goes just before the last group, which holds what belongs to the whole installation.
      group = { ...FALLBACK_GROUP, pages: [] };
      const order = [...groups.values()];
      order.splice(Math.max(order.length - 1, 0), 0, group);
      groups.clear();
      for (const item of order) groups.set(item.id, item);
    }
    group.pages.push(page);
  }
  for (const { parent, part } of parts) pages.get(parent)?.parts.push(part);

  const out = [...groups.values()].filter((group) => group.pages.length > 0);
  // With no layout from the server there is one group, and a heading over it would say nothing.
  return layout.groups.length === 0 ? out.map((group) => ({ ...group, label: "" })) : out;
}

export function navPages(groups: readonly NavGroup[]): NavPage[] {
  return groups.flatMap((group) => group.pages);
}

export type SettingsTarget = {
  /** The page to show. */
  page: string;
  /** The part of that page to scroll to, when the link names one. */
  part: string | null;
  /** The setting to scroll to and point out, when the link names one. */
  setting: string | null;
};

/**
 * What a settings link points at. The sub-route is `<section>` or
 * `<section>/<setting key>`. A part's own id (an older link such as
 * `village_sleep`) opens its page at that part. A setting is found by its key
 * wherever it lives now, so a link survives the setting moving to another
 * section. Anything unknown opens the first page.
 */
export function resolveTarget(sub: string | null, groups: readonly NavGroup[], settings: readonly Pick<Setting, "key" | "section">[]): SettingsTarget | null {
  const pages = navPages(groups);
  const first = pages[0];
  if (!first) return null;
  const pageOf = new Map<string, string>();
  for (const page of pages) {
    pageOf.set(page.id, page.id);
    for (const part of page.parts) pageOf.set(part.id, page.id);
  }
  const at = (section: string, setting: string | null): SettingsTarget | null => {
    const page = pageOf.get(section);
    return page === undefined ? null : { page, part: page === section ? null : section, setting };
  };
  const forSetting = (key: string): SettingsTarget | null => {
    const found = settings.find((setting) => setting.key === key);
    return found ? at(found.section, found.key) : null;
  };

  const fallback: SettingsTarget = { page: first.id, part: null, setting: null };
  if (!sub) return fallback;
  const slash = sub.indexOf("/");
  const head = slash < 0 ? sub : sub.slice(0, slash);
  const rest = slash < 0 ? "" : sub.slice(slash + 1);
  if (rest) {
    const target = forSetting(rest);
    if (target) return target;
  }
  const direct = at(head, null) ?? forSetting(head);
  if (direct) return direct;
  // A section that is gone (its settings moved elsewhere): open where they are now.
  const moved = settings.find((setting) => setting.key.startsWith(`${head}.`));
  return (moved ? at(moved.section, null) : null) ?? fallback;
}

/** The link to a section, or to one setting in it. */
export function settingsHash(section: string, settingKey?: string): string {
  return formatHash({ screen: "settings", sub: settingKey ? `${section}/${settingKey}` : section });
}

export type PageBlock<S> = {
  /** The section these settings belong to. */
  id: string;
  /** The heading over the block; empty when the page heading is enough. */
  label: string;
  description: string;
  rows: S[];
};

/**
 * A page's settings in headed blocks: its own first, then each part that has
 * any. `ownLabel` is the heading over the page's own settings when something
 * else on the page needs telling apart from them.
 */
export function pageBlocks<S extends Pick<Setting, "section">>(page: NavPage, settings: readonly S[], ownLabel = ""): PageBlock<S>[] {
  const blocks: PageBlock<S>[] = [];
  const own = settings.filter((setting) => setting.section === page.id);
  const parts = page.parts
    .map((part) => ({ ...part, rows: settings.filter((setting) => setting.section === part.id) }))
    .filter((part) => part.rows.length > 0);
  if (own.length > 0) blocks.push({ id: page.id, label: ownLabel || (parts.length > 0 ? "General" : ""), description: "", rows: own });
  return [...blocks, ...parts];
}

/** The "on this page" list: only worth showing when there is more than one block to jump between. */
export function onThisPage<S>(blocks: readonly PageBlock<S>[]): { id: string; label: string; count: number }[] {
  if (blocks.length < 2) return [];
  return blocks.map((block) => ({ id: block.id, label: block.label, count: block.rows.length }));
}

function domSafe(text: string): string {
  return text.replace(/[^a-zA-Z0-9_-]/g, "-");
}

/** The id of the control for a setting. */
export function settingControlId(key: string): string {
  return `setting-${domSafe(key)}`;
}

/** The id of the whole row for a setting: what a link to that setting scrolls to. */
export function settingRowId(key: string): string {
  return `setting-row-${domSafe(key)}`;
}

/** The id of the heading over a block of a page. */
export function blockHeadingId(section: string): string {
  return `settings-part-${domSafe(section)}`;
}
