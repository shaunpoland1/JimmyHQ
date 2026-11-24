import { Check, Copy } from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { useToast } from "../../hooks/useToast";
import { safeWebUrl } from "../lookup/lib/url";
import { linkBareAddresses } from "./lib/web";

const COPIED_MS = 1600;

/** Copies text, with a toast if the browser refuses. Returns whether it worked. */
export function useCopy(): (text: string) => Promise<boolean> {
  const toast = useToast();
  return async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      toast.error("The browser did not allow copying. Select the text and copy it yourself.");
      return false;
    }
  };
}

/** A small copy button that shows a tick for a moment after it worked. */
export function CopyButton({ getText, label, className = "" }: { getText: () => string; label: string; className?: string }) {
  const copy = useCopy();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <button
      type="button"
      className={`icon-btn ${className}`}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      onClick={() => void copy(getText()).then((ok) => setCopied(ok))}
    >
      {copied ? <Check size={14} className="text-success" aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
    </button>
  );
}

function CodeBlock({ children, node: _node, ...rest }: ComponentPropsWithoutRef<"pre"> & { node?: unknown }) {
  const pre = useRef<HTMLPreElement>(null);
  return (
    <div className="chat-code">
      <pre ref={pre} tabIndex={0} {...rest}>
        {children}
      </pre>
      <CopyButton className="chat-copy" label="Copy code" getText={() => pre.current?.textContent ?? ""} />
    </div>
  );
}

// A model's words are untrusted. react-markdown never renders raw HTML and
// drops unsafe link addresses by default; on top of that, a picture is shown
// as a link, so that showing a reply never fetches an address the model chose.
const COMPONENTS: Components = {
  // Only an ordinary web address is a link; anything else (a script, a relative address) is shown as its words.
  a: ({ children, href, node: _node, ...rest }) => {
    const safe = safeWebUrl(href);
    return safe ? (
      <a {...rest} href={safe} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    );
  },
  img: ({ src, alt }) => {
    const safe = safeWebUrl(src);
    return safe ? (
      <a href={safe} target="_blank" rel="noopener noreferrer">
        {alt || "image"}
      </a>
    ) : (
      <span>{alt}</span>
    );
  },
  pre: CodeBlock,
};

/** A model's reply, rendered from Markdown with the library's safe defaults. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="chat-md">
      <ReactMarkdown components={COMPONENTS}>{linkBareAddresses(text)}</ReactMarkdown>
    </div>
  );
});
