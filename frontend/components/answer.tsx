"use client";

import ReactMarkdown from "react-markdown";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { Button, buttonVariants } from "@/components/ui/button";
import type { AskResponse, CitationItem } from "@/lib/api";

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

interface AnswerViewProps {
  response: AskResponse;
  sourceMap: Map<string, string>;
  onOpenPdf: (sourceId: string, title: string, page: number) => void;
}

export function AnswerView({ response, sourceMap, onOpenPdf }: AnswerViewProps) {
  const courseParagraphs = response.paragraphs.filter((p) => !p.outside_course);
  const outsideParagraphs = response.paragraphs.filter((p) => p.outside_course);

  const renderCitationButton = (citation: CitationItem, index: number) => {
    const openTarget = citation.open;
    if (openTarget.kind === "pdf") {
      const title = sourceMap.get(openTarget.source_id) ?? citation.label;
      const page = openTarget.page;
      return (
        <Button
          key={citation.chunk_id || `${citation.label}-${index}`}
          variant="outline"
          size="sm"
          onClick={() => onOpenPdf(openTarget.source_id, title, page)}
          className="text-xs"
        >
          {citation.label}
        </Button>
      );
    }

    if (openTarget.kind === "youtube" || "url" in openTarget) {
      const url = "url" in openTarget ? (openTarget.url as string) : "";
      return (
        <a
          key={citation.chunk_id || `${citation.label}-${index}`}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonVariants({ variant: "outline", size: "sm" }) + " text-xs"}
        >
          {citation.label}
        </a>
      );
    }

    return null;
  };

  const renderParagraph = (
    p: (typeof response.paragraphs)[number],
    pIndex: number
  ) => {
    const normalizedText = normalizeMathDelimiters(p.text);
    return (
      <div key={p.id || `p-${pIndex}`} className="space-y-2">
        <div className="text-sm leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkMath]}
            rehypePlugins={[rehypeKatex]}
          >
            {normalizedText}
          </ReactMarkdown>
        </div>
        {p.citations && p.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {p.citations.map((c, cIdx) => renderCitationButton(c, cIdx))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {/* Course-grounded paragraphs */}
      {courseParagraphs.length > 0 && (
        <div className="space-y-3">
          {courseParagraphs.map((p, idx) => renderParagraph(p, idx))}
        </div>
      )}

      {/* Outside course paragraphs (including not-covered paragraph when allow_outside=false) */}
      {outsideParagraphs.length > 0 && (
        <div className="border rounded-lg p-4 space-y-3 bg-muted/20">
          <div>
            <h4 className="text-sm font-semibold">Beyond your course</h4>
            <p className="text-xs text-muted-foreground">Not from your sources.</p>
          </div>
          <div className="space-y-3">
            {outsideParagraphs.map((p, idx) => renderParagraph(p, idx))}
          </div>
        </div>
      )}

      {/* Collapsed passages used */}
      {response.context && response.context.length > 0 && (
        <details className="text-xs text-muted-foreground border-t pt-2">
          <summary className="cursor-pointer font-medium hover:text-foreground">
            Passages used ({response.context.length})
          </summary>
          <div className="mt-2 space-y-2 pl-2 border-l">
            {response.context.map((chunk, idx) => {
              const pageStr =
                chunk.loc.page != null ? `p. ${chunk.loc.page}` : "p. N/A";
              const scoreStr = chunk.score.toFixed(2);
              const previewText = chunk.text.slice(0, 200);
              return (
                <div key={chunk.chunk_id || `chunk-${idx}`} className="space-y-0.5">
                  <div className="font-mono text-[11px] text-foreground">
                    [{pageStr}, score: {scoreStr}]
                  </div>
                  <p className="text-muted-foreground line-clamp-3">
                    {previewText}
                    {chunk.text.length > 200 ? "…" : ""}
                  </p>
                </div>
              );
            })}
          </div>
        </details>
      )}
    </div>
  );
}
