import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

type EmptyStateProps = {
  icon: LucideIcon;
  title: string;
  description: string;
  /** Heading level: 1 for a whole screen, 2 inside a panel. */
  level?: 1 | 2;
  compact?: boolean;
  children?: ReactNode;
};

/** A calm placeholder: muted icon, title and one line of description. */
export function EmptyState({ icon: Icon, title, description, level = 2, compact = false, children }: EmptyStateProps) {
  const Heading = level === 1 ? "h1" : "h2";
  return (
    <div className={`flex h-full flex-col items-center justify-center text-center ${compact ? "gap-2 p-6" : "gap-3 p-8"}`}>
      <div
        className={`flex items-center justify-center rounded-full border border-border bg-surface text-text-faint ${compact ? "size-12" : "size-16"}`}
        aria-hidden="true"
      >
        <Icon size={compact ? 20 : 28} strokeWidth={1.5} />
      </div>
      <Heading className={`font-semibold text-text ${compact ? "text-sm" : "text-lg"}`}>{title}</Heading>
      <p className={`max-w-sm text-text-muted ${compact ? "text-xs" : "text-sm"}`}>{description}</p>
      {children}
    </div>
  );
}
