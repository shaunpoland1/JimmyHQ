import { X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Open dialogs, oldest first. Only the last one answers Escape and traps Tab,
// so a dialog opened from inside another closes on its own.
const openModals: symbol[] = [];

type ModalProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** Hide the visible header; the title is still announced to screen readers. */
  bare?: boolean;
  placement?: "center" | "top";
  widthClass?: string;
  /** Replaces the default size of the panel, for dialogs that are not small. */
  panelClass?: string;
};

/**
 * Styled dialog used instead of native alert/confirm/prompt. Traps focus,
 * closes on Escape or a backdrop click, and restores focus when it closes.
 * Mount it only while it should be open.
 */
export function Modal({ title, onClose, children, bare = false, placement = "center", widthClass = "max-w-md", panelClass }: ModalProps) {
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const node = panel.current;
    const focusables = () => Array.from(node?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    const first = node?.querySelector<HTMLElement>("[data-autofocus]") ?? focusables()[0] ?? node;
    first?.focus();

    const token = Symbol("modal");
    openModals.push(token);

    const onKeyDown = (event: KeyboardEvent) => {
      if (openModals[openModals.length - 1] !== token) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      const head = items[0];
      const tail = items[items.length - 1];
      if (!head || !tail) {
        event.preventDefault();
        return;
      }
      const active = document.activeElement;
      if (event.shiftKey && (active === head || !node?.contains(active))) {
        event.preventDefault();
        tail.focus();
      } else if (!event.shiftKey && (active === tail || !node?.contains(active))) {
        event.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      const at = openModals.indexOf(token);
      if (at >= 0) openModals.splice(at, 1);
      previous?.focus();
    };
  }, []);

  return (
    <div
      className={`fixed inset-0 z-50 flex justify-center bg-black/35 p-4 ${placement === "top" ? "items-start pt-[12vh]" : "items-center"}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`flex ${panelClass ?? `max-h-[80vh] w-full ${widthClass}`} flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-pop`}
      >
        {bare ? (
          <h2 id={titleId} className="sr-only">
            {title}
          </h2>
        ) : (
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <h2 id={titleId} className="text-sm font-semibold">
              {title}
            </h2>
            <button type="button" className="icon-btn" onClick={onClose} aria-label="Close dialog">
              <X size={16} />
            </button>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
