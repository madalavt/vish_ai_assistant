"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";

import { CopyButton } from "@/components/chat/copy-button";

/**
 * Memoized because it re-renders on every streamed token otherwise, and
 * re-parsing markdown per token is the main cause of janky streaming.
 */
export const Markdown = memo(function Markdown({
  content,
}: {
  content: string;
}) {
  return (
    <div className="prose-chat">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[
          [rehypeHighlight, { detect: true, ignoreMissing: true }],
        ]}
        components={{
          pre({ children, ...props }) {
            const code = extractText(children);
            return (
              <div className="group relative">
                <pre {...props}>{children}</pre>
                <div className="absolute top-2 right-2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                  <CopyButton value={code} />
                </div>
              </div>
            );
          },
          a({ children, ...props }) {
            return (
              <a {...props} target="_blank" rel="noreferrer noopener">
                {children}
              </a>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

/** Pull plain text out of the React tree so the copy button has something to copy. */
function extractText(node: unknown): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return extractText(
      (node as { props: { children?: unknown } }).props?.children,
    );
  }
  return "";
}
