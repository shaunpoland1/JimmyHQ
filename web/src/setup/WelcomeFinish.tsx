import { Bot, LayoutGrid, ListTodo, MessageSquare } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { formatHash } from "../lib/route";
import { FINISH_SCREENS } from "../lib/welcome";

type WelcomeFinishProps = {
  chatReady: boolean;
  busy: boolean;
  failure: string | null;
  onBack: () => void;
  /** Saves "finished"; the link then opens the screen it names. */
  onFinish: () => void;
};

const STEPS: Record<(typeof FINISH_SCREENS)[number], { href: string; icon: LucideIcon; title: string; detail: string }> = {
  chat: { href: formatHash({ screen: "chat", sub: "new" }), icon: MessageSquare, title: "Start a chat", detail: "Ask a question or think something through with the model." },
  tasks: { href: formatHash({ screen: "tasks", sub: null }), icon: ListTodo, title: "Add a task", detail: "Write down something you need to do, and when." },
  village: { href: formatHash({ screen: "village", sub: null }), icon: Bot, title: "Look at the Village", detail: "Meet your agents and see what each one is doing." },
  apps: { href: formatHash({ screen: "apps", sub: null }), icon: LayoutGrid, title: "Browse the Apps", detail: "JimmyHQ comes with apps for your books, parcels, photos and more. Install the ones you want from the Apps screen." },
};

const NEXT = FINISH_SCREENS.map((screen) => STEPS[screen]);

const MODELS_HREF = formatHash({ screen: "settings", sub: "models" });

/** Step 4: you're set, and a few things to try next, the Apps screen among them. */
export function WelcomeFinish({ chatReady, busy, failure, onBack, onFinish }: WelcomeFinishProps) {
  return (
    <div className="flex flex-col gap-4">
      {!chatReady ? (
        <p className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-text">
          No model is connected yet, so chat and your agents cannot answer.{" "}
          <a href={MODELS_HREF} onClick={onFinish} className="underline underline-offset-2">
            Connect one in Settings, Models and roles
          </a>
          .
        </p>
      ) : null}
      <ul className="flex flex-col gap-2">
        {NEXT.map(({ href, icon: Icon, title, detail }) => (
          <li key={href}>
            <a
              href={href}
              onClick={onFinish}
              className="flex items-start gap-3 rounded-xl border border-border bg-bg px-3 py-2.5 hover:border-accent focus-visible:border-accent"
            >
              <Icon size={16} className="mt-0.5 flex-none text-text-muted" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{title}</span>
                <span className="block text-xs text-text-muted">{detail}</span>
              </span>
            </a>
          </li>
        ))}
      </ul>
      {failure ? (
        <p role="alert" className="text-xs text-danger">
          {failure}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <button type="button" className="btn" onClick={onBack} disabled={busy}>
          Back
        </button>
        <button type="button" className="btn btn-primary" onClick={onFinish} disabled={busy}>
          {busy ? "Saving…" : "Go to JimmyHQ"}
        </button>
      </div>
    </div>
  );
}
