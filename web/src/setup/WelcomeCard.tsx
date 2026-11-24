import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Logo } from "../components/Logo";

type WelcomeCardProps = {
  /** "Step 2 of 4". */
  step: string;
  title: string;
  subtitle: ReactNode;
  /** Room for the provider list on the model step. */
  wide?: boolean;
  children: ReactNode;
  /** A quiet line under the card, such as "Skip the welcome steps". */
  footer?: ReactNode;
};

/** The centred card of the welcome steps, in the same style as the setup and sign-in card. */
export function WelcomeCard({ step, title, subtitle, wide = false, children, footer }: WelcomeCardProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  // Each step starts with the keyboard and screen reader on its heading.
  useEffect(() => {
    heading.current?.focus();
  }, [title]);

  return (
    <main className="flex h-full flex-col items-center overflow-auto bg-bg px-4 py-6 sm:py-10">
      <div className={`my-auto w-full ${wide ? "max-w-2xl" : "max-w-md"}`}>
        <div className="rounded-2xl border border-border bg-surface p-5 shadow-pop sm:p-8">
          <div className="mb-6 flex flex-col items-center gap-3 text-center">
            <Logo size={44} />
            <div>
              <p className="font-mono text-[10.5px] uppercase tracking-widest text-text-faint">{step}</p>
              <h1 ref={heading} tabIndex={-1} className="mt-1 text-lg font-semibold outline-none">
                {title}
              </h1>
              <p className="mt-1 text-sm text-text-muted">{subtitle}</p>
            </div>
          </div>
          {children}
        </div>
        {footer ? <div className="mt-3 text-center text-xs text-text-faint">{footer}</div> : null}
      </div>
    </main>
  );
}
