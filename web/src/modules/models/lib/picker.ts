// The model picker's options, its flat list of choices and its keyboard map.

export type PickerTarget = { kind: "role"; role: string } | { kind: "model"; provider_id: string; model: string };

export type ModelPickerValue = { target: PickerTarget; effort: string | null };

/** What a role currently maps to. `local` is null when the role is not set up. */
export type PickerRole = {
  role: string;
  provider_label: string | null;
  model: string | null;
  local: boolean | null;
  /** The effort the role is set up with (null: none set). Left out when the caller does not know it. */
  effort?: string | null;
};

export type PickerProvider = { id: string; label: string; local: boolean; models: string[] };

/** Supplied by the caller: the roles, the providers with their models, and the efforts offered. */
export type ModelPickerOptions = {
  roles: PickerRole[];
  providers: PickerProvider[];
  efforts: string[];
};

export type PickerItem = {
  key: string;
  /** Heading the item sits under: "Roles" or a provider's label. */
  group: string;
  label: string;
  detail: string;
  /** null when it is not known where the choice runs. */
  isLocal: boolean | null;
  target: PickerTarget;
};

export function targetKey(target: PickerTarget): string {
  return target.kind === "role" ? `role:${target.role}` : `model:${target.provider_id}\n${target.model}`;
}

export function sameTarget(a: PickerTarget, b: PickerTarget): boolean {
  return targetKey(a) === targetKey(b);
}

function roleDetail(role: PickerRole): string {
  if (!role.model) return "not set up";
  return role.provider_label ? `${role.model} · ${role.provider_label}` : role.model;
}

/** Every choice in display order: roles first, then each provider's models. */
export function pickerItems(options: ModelPickerOptions): PickerItem[] {
  const items: PickerItem[] = [];
  for (const role of options.roles) {
    const { local } = role;
    const target: PickerTarget = { kind: "role", role: role.role };
    items.push({ key: targetKey(target), group: "Roles", label: role.role, detail: roleDetail(role), isLocal: local, target });
  }
  for (const provider of options.providers) {
    const { local } = provider;
    for (const model of provider.models) {
      const target: PickerTarget = { kind: "model", provider_id: provider.id, model };
      items.push({ key: targetKey(target), group: provider.label, label: model, detail: provider.label, isLocal: local, target });
    }
  }
  return items;
}

export type PickSummary = { label: string; detail: string; isLocal: boolean | null };

/** What the closed picker shows for the current value, even when it is not among the options. */
export function describePick(target: PickerTarget, options: ModelPickerOptions): PickSummary {
  const found = pickerItems(options).find((item) => item.key === targetKey(target));
  if (found) return { label: found.label, detail: found.detail, isLocal: found.isLocal };
  if (target.kind === "role") return { label: target.role, detail: "not set up", isLocal: null };
  const provider = options.providers.find((item) => item.id === target.provider_id);
  if (!provider) return { label: target.model, detail: target.provider_id, isLocal: null };
  const { local } = provider;
  return { label: target.model, detail: provider.label, isLocal: local };
}

/**
 * What the effort control says while the owner has not picked one: the effort
 * the chosen role is set up with, so it is clear what will be used. Plain
 * "effort" when the caller did not say what roles are set up with.
 */
export function unsetEffortLabel(target: PickerTarget, options: Pick<ModelPickerOptions, "roles">): string {
  if (!options.roles.some((role) => role.effort !== undefined)) return "effort";
  if (target.kind === "model") return "Effort: model's own default";
  const role = options.roles.find((item) => item.role === target.role);
  return `Effort: ${role?.effort ?? "model's own default"}`;
}

export function localityWord(isLocal: boolean | null): string {
  return isLocal === null ? "unknown" : isLocal ? "local" : "hosted";
}

/**
 * How far to move the list sideways (in pixels, never to the right) so it
 * stays inside the window when the control sits near the right edge.
 * `left` is the control's left edge in the window.
 */
export function popupShift(left: number, windowWidth: number, popupWidth: number, margin: number): number {
  const width = Math.min(popupWidth, windowWidth - 2 * margin);
  const room = windowWidth - margin - width - left;
  return Math.min(0, Math.max(room, margin - left));
}

const PAGE = 8;

/**
 * Where a key moves the highlight in a list of `count` items, or null when
 * the key is not a movement key. Arrow keys wrap around.
 */
export function moveHighlight(key: string, index: number, count: number): number | null {
  if (count <= 0) return null;
  const at = index < 0 || index >= count ? -1 : index;
  switch (key) {
    case "ArrowDown":
      return at === -1 ? 0 : (at + 1) % count;
    case "ArrowUp":
      return at === -1 ? count - 1 : (at - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    case "PageDown":
      return Math.min(count - 1, Math.max(at, 0) + PAGE);
    case "PageUp":
      return Math.max(0, (at === -1 ? 0 : at) - PAGE);
    default:
      return null;
  }
}

/** The next item whose label starts with what was typed, looking after `from` and wrapping. */
export function typeahead(items: readonly Pick<PickerItem, "label">[], typed: string, from: number): number | null {
  const query = typed.trim().toLowerCase();
  if (!query || items.length === 0) return null;
  for (let step = 1; step <= items.length; step += 1) {
    const index = (from + step + items.length) % items.length;
    if (items[index]?.label.toLowerCase().startsWith(query)) return index;
  }
  return null;
}

type RoleLike = { role: string; primary: { provider_id: string; model: string } | null; inherits_default: boolean };
type ProviderLike = { id: string; label: string; enabled: boolean; local: boolean };

/**
 * Builds the picker's options from what the server returns: each role with
 * what it maps to, and each enabled provider with its discovered models.
 */
export function buildPickerOptions(
  roles: readonly RoleLike[],
  providers: readonly ProviderLike[],
  modelsByProvider: Readonly<Record<string, readonly string[] | undefined>>,
  efforts: readonly string[],
): ModelPickerOptions {
  const byId = new Map(providers.map((provider) => [provider.id, provider]));
  const fallbackRole = roles.find((entry) => entry.role === "default");
  return {
    roles: roles.map((entry) => {
      const primary = entry.primary ?? (entry.inherits_default ? (fallbackRole?.primary ?? null) : null);
      if (!primary) return { role: entry.role, provider_label: null, model: null, local: null };
      const provider = byId.get(primary.provider_id);
      if (!provider) return { role: entry.role, provider_label: primary.provider_id, model: primary.model, local: null };
      const { local } = provider;
      return { role: entry.role, provider_label: provider.label, model: primary.model, local };
    }),
    providers: providers
      .filter((provider) => provider.enabled)
      .map((provider) => {
        const { local } = provider;
        return { id: provider.id, label: provider.label, local, models: [...(modelsByProvider[provider.id] ?? [])] };
      }),
    efforts: [...efforts],
  };
}
