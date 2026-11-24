import { memo } from "react";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import { safeHref } from "./lib/markdown";

// What an agent wrote is untrusted. react-markdown never renders raw HTML; on top of that
// only web and mail links are kept, and a picture is shown as a link, so reading a file
// never fetches an address the file chose.
const COMPONENTS: Components = {
  a: ({ children, href }) =>
    href ? (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  img: ({ src, alt }) =>
    typeof src === "string" && src ? (
      <a href={src} target="_blank" rel="noreferrer noopener">
        {alt || "image"}
      </a>
    ) : (
      <span>{alt}</span>
    ),
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
};

const urlTransform = (url: string) => safeHref(url) ?? "";

/** A markdown file, rendered for reading. */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="village-md">
      <ReactMarkdown components={COMPONENTS} skipHtml urlTransform={urlTransform}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
