// The editable price table: drafts, validation and the list to save.

import type { PriceEntry } from "../api.ts";

export const MAX_PRICE = 1_000_000;
export const MAX_PRICE_LINES = 500;

export type PriceDraft = {
  /** Stable key for the row on screen. */
  key: string;
  /** The saved line's id; empty for a line added on screen. */
  id: string;
  provider: string;
  model: string;
  input: string;
  output: string;
};

/**
 * A saved price as the owner would type it. Very small numbers print as
 * "1e-7" by default, which the price field does not accept.
 */
export function priceText(value: number): string {
  const text = String(value);
  if (!/e/i.test(text)) return text;
  const fixed = value.toFixed(20);
  return fixed.includes(".") ? fixed.replace(/0+$/, "").replace(/\.$/, "") : fixed;
}

export function toDrafts(prices: readonly PriceEntry[]): PriceDraft[] {
  return prices.map((entry) => ({
    key: `saved:${entry.id}`,
    id: entry.id,
    provider: entry.provider,
    model: entry.model,
    input: priceText(entry.input_per_mtok),
    output: priceText(entry.output_per_mtok),
  }));
}

export function newDraft(sequence: number): PriceDraft {
  return { key: `new:${sequence}`, id: "", provider: "*", model: "", input: "", output: "" };
}

/** A price typed by the owner, or null when it is not a number from 0 to 1,000,000. */
export function parsePrice(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(\.\d+)?$|^\.\d+$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 && value <= MAX_PRICE ? value : null;
}

export type PriceErrors = Partial<Record<"provider" | "model" | "input" | "output", string>>;

const CONTROL = /[\u0000-\u001f\u007f]/;
const NUMBER_HINT = "Enter a number from 0 to 1000000.";

export function priceErrors(draft: PriceDraft): PriceErrors {
  const errors: PriceErrors = {};
  const provider = draft.provider.trim();
  if (!provider) errors.provider = "Enter a provider type, a provider id, or * for any.";
  else if (provider.length > 40 || CONTROL.test(provider)) errors.provider = "At most 40 characters.";
  const model = draft.model.trim();
  if (!model) errors.model = "Enter a model pattern. * matches anything.";
  else if (model.length > 200 || CONTROL.test(model)) errors.model = "At most 200 characters.";
  if (parsePrice(draft.input) === null) errors.input = NUMBER_HINT;
  if (parsePrice(draft.output) === null) errors.output = NUMBER_HINT;
  return errors;
}

function slug(text: string): string {
  let made = text
    .toLowerCase()
    .replace(/\*/g, " any ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  if (!made) made = "price";
  if (!/^[a-z]/.test(made)) made = `p-${made}`;
  return made.slice(0, 36).replace(/-+$/, "");
}

/** An id for a new line that no other line uses. */
export function makePriceId(provider: string, model: string, taken: ReadonlySet<string>): string {
  const base = slug(`${provider}-${model}`);
  if (!taken.has(base)) return base;
  for (let n = 2; ; n += 1) {
    const candidate = `${base}-${n}`;
    if (!taken.has(candidate)) return candidate;
  }
}

export type PriceBuild = { ok: true; prices: PriceEntry[] } | { ok: false; errors: PriceErrors[]; summary: string };

/** Checks every line and builds the whole table to save, giving new lines an id. */
export function buildPrices(drafts: readonly PriceDraft[]): PriceBuild {
  const errors = drafts.map(priceErrors);
  if (drafts.length > MAX_PRICE_LINES) {
    return { ok: false, errors, summary: `A price table can have at most ${MAX_PRICE_LINES} lines.` };
  }
  const bad = errors.filter((entry) => Object.keys(entry).length > 0).length;
  if (bad > 0) {
    return { ok: false, errors, summary: bad === 1 ? "One line needs fixing before the table can be saved." : `${bad} lines need fixing before the table can be saved.` };
  }
  const taken = new Set(drafts.map((draft) => draft.id).filter(Boolean));
  const prices = drafts.map((draft) => {
    const provider = draft.provider.trim();
    const model = draft.model.trim();
    let id = draft.id;
    if (!id) {
      id = makePriceId(provider, model, taken);
      taken.add(id);
    }
    return { id, provider, model, input_per_mtok: parsePrice(draft.input) ?? 0, output_per_mtok: parsePrice(draft.output) ?? 0 };
  });
  return { ok: true, prices };
}

/** Whether the drafts differ from the saved table (order included). */
export function pricesChanged(drafts: readonly PriceDraft[], saved: readonly PriceEntry[]): boolean {
  if (drafts.length !== saved.length) return true;
  return drafts.some((draft, index) => {
    const entry = saved[index];
    return (
      !entry ||
      draft.id !== entry.id ||
      draft.provider.trim() !== entry.provider ||
      draft.model.trim() !== entry.model ||
      parsePrice(draft.input) !== entry.input_per_mtok ||
      parsePrice(draft.output) !== entry.output_per_mtok
    );
  });
}

/** How a price line is named in a chooser. */
export function priceLabel(entry: Pick<PriceEntry, "provider" | "model">): string {
  return `${entry.provider === "*" ? "any provider" : entry.provider} · ${entry.model}`;
}
