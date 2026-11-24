// The one call that takes the owner to an app's own page under Settings: `hq.ui.settings()`.
// The page names nothing: the shell builds the address from the app it mounted, and only on
// the owner's own touch, so a page cannot throw Settings open by itself.

import type { BridgeError } from "./bridge.ts";
import { settingsHash } from "./route.ts";
import { gestureAllows } from "./uploads.ts";
import type { Activation } from "./uploads.ts";

export const SETTINGS_REFUSALS = {
  noGesture: "The owner has not touched your page just now, so Settings was not opened. Call hq.ui.settings from the owner's own click on a button.",
} as const;

/**
 * Where `ui.settings` goes for this app, or the refusal. Pure: the caller sets the address.
 * Settings opens over the screen that is showing, so what the owner typed there is kept.
 */
export function ownSettingsTarget(
  app: { id: string; settings_section?: string | null },
  activation: Activation,
  touchedAt: number | null,
  now: number,
): { ok: true; hash: string } | { ok: false; error: BridgeError } {
  if (!gestureAllows(activation, touchedAt, now)) {
    return { ok: false, error: { code: "not_allowed", message: SETTINGS_REFUSALS.noGesture } };
  }
  return { ok: true, hash: settingsHash(app) };
}
