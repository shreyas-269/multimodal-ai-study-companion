"use client";

import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/**
 * Converts LaTeX delimiters outside code blocks from \( ... \) to $...$
 * and \[ ... \] to $$...$$, so remark-math and rehype-katex render them cleanly.
 */
export function normalizeMathDelimiters(text: string): string {
  const tokens = text.split(/(```[\s\S]*?```|`[^`\n]*?`)/g);
  return tokens
    .map((token, i) => {
      // Odd indices are code snippets (fenced or inline); leave untouched
      if (i % 2 === 1) return token;
      let out = token.replace(/\\\[([\s\S]*?)\\\]/g, (_, math) => `$$${math}$$`);
      out = out.replace(/\\\(([\s\S]*?)\\\)/g, (_, math) => `$${math}$`);
      return out;
    })
    .join("");
}

const DISALLOWED_INLINE_ELEMENTS = [
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "ul",
  "ol",
  "li",
  "blockquote",
  "pre",
  "table",
  "hr",
  "img",
];

export interface MarkdownMathProps {
  content: string;
  inline?: boolean;
  className?: string;
}

export function MarkdownMath({
  content,
  inline = false,
  className,
}: MarkdownMathProps) {
  const normalized = normalizeMathDelimiters(content);

  if (inline) {
    return (
      <span className={className}>
        <ReactMarkdown
          remarkPlugins={[remarkMath]}
          rehypePlugins={[rehypeKatex]}
          disallowedElements={DISALLOWED_INLINE_ELEMENTS}
          unwrapDisallowed
          components={{
            p: ({ children }) => <span>{children}</span>,
          }}
        >
          {normalized}
        </ReactMarkdown>
      </span>
    );
  }

  // Block mode: renders exactly what components/answer.tsx renders today
  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath]}
      rehypePlugins={[rehypeKatex]}
    >
      {normalized}
    </ReactMarkdown>
  );
}
