"use client";

import { CitationChip } from "@/components/citation-chip";
import { formatCitationTime } from "@/lib/format";
import type { AskResponse } from "@/lib/api";
import { MarkdownMath, normalizeMathDelimiters } from "@/components/markdown-math";

export { normalizeMathDelimiters };

interface AnswerViewProps {
  response: AskResponse;
  sourceMap: Map<string, string>;
}

export function AnswerView({ response, sourceMap }: AnswerViewProps) {
  const courseParagraphs = response.paragraphs.filter((p) => !p.outside_course);
  const outsideParagraphs = response.paragraphs.filter((p) => p.outside_course);

  const renderParagraph = (
    p: (typeof response.paragraphs)[number],
    pIndex: number
  ) => {
    return (
      <div key={p.id || `p-${pIndex}`} className="space-y-2">
        <div className="text-sm leading-relaxed">
          <MarkdownMath content={p.text} />
        </div>
        {p.citations && p.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {p.citations.map((c, cIdx) => (
              <CitationChip
                key={c.chunk_id || `${c.label}-${cIdx}`}
                citation={c}
                title={c.open.kind === "pdf" ? sourceMap.get(c.open.source_id) : undefined}
              />
            ))}
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
              const isVideo =
                chunk.loc.t_start_s != null && chunk.loc.page == null;
              let locLabel: string;
              if (isVideo) {
                const title =
                  (chunk.loc.source_id
                    ? sourceMap.get(chunk.loc.source_id)
                    : undefined) || "Lecture video";
                const timeStr = formatCitationTime(chunk.loc.t_start_s!);
                locLabel = `${title}, ${timeStr}`;
              } else {
                locLabel =
                  chunk.loc.page != null ? `p. ${chunk.loc.page}` : "p. N/A";
              }
              const scoreStr =
                typeof chunk.score === "number" && !isNaN(chunk.score)
                  ? chunk.score.toFixed(2)
                  : "0.00";
              const previewText = chunk.text.slice(0, 200);
              return (
                <div key={chunk.chunk_id || `chunk-${idx}`} className="space-y-0.5">
                  <div className="font-mono text-[11px] text-foreground">
                    [{locLabel}, score: {scoreStr}]
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
