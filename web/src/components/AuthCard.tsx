import type { ReactNode } from "react";
import { Logo } from "./Logo";

type AuthCardProps = { title: string; subtitle: string; children: ReactNode };

/** Centred card shared by the setup and sign-in screens. */
export function AuthCard({ title, subtitle, children }: AuthCardProps) {
  return (
    <main className="flex h-full items-center justify-center overflow-auto bg-bg p-4">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-surface p-6 shadow-pop sm:p-8">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <Logo size={44} />
          <div>
            <h1 className="text-lg font-semibold">{title}</h1>
            <p className="mt-1 text-sm text-text-muted">{subtitle}</p>
          </div>
        </div>
        {children}
      </div>
    </main>
  );
}
