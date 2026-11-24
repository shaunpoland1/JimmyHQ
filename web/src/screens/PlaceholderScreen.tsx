import { EmptyState } from "../components/EmptyState";
import { SCREEN_ICONS } from "../components/screenIcons";
import { screenMeta } from "../lib/screens";
import type { ScreenId } from "../lib/screens";

/** Shared empty state for screens that are not built yet. */
export function PlaceholderScreen({ screen }: { screen: ScreenId }) {
  const meta = screenMeta(screen);
  return (
    <EmptyState icon={SCREEN_ICONS[screen]} title={meta.label} description={meta.description} level={1}>
      <p className="text-xs text-text-faint">Nothing here yet.</p>
    </EmptyState>
  );
}
