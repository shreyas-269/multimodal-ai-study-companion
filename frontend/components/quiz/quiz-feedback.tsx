"use client";

import React from "react";
import { CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import type { NormalisedQuestion, NormalisedFeedback } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { MarkdownMath } from "@/components/markdown-math";
import { CitationChip } from "@/components/citation-chip";
import type { QuestionResult, QuizErrorState } from "./quiz-types";

export interface QuizFeedbackProps {
  question: NormalisedQuestion;
  result: QuestionResult;
  onNext: () => void;
  isLastQuestion: boolean;
  isFinishing: boolean;
  headingRef?: React.RefObject<HTMLHeadingElement | null>;
  finishError?: QuizErrorState | null;
  onRetryFinish?: () => void;
  onBackToTopics?: () => void;
}

function formatVerdict(verdict: NormalisedFeedback["verdict"]): string {
  switch (verdict) {
    case "correct":
      return "Correct";
    case "incorrect":
      return "Not quite";
    case "partial":
      return "Partly right";
    default: {
      const _exhaustive: never = verdict;
      return _exhaustive;
    }
  }
}

export function QuizFeedback({
  result,
  onNext,
  isLastQuestion,
  isFinishing,
  headingRef,
  finishError,
  onRetryFinish,
  onBackToTopics,
}: QuizFeedbackProps) {
  const verdictWord = formatVerdict(result.feedback.verdict);

  return (
    <div className="rounded-lg border p-6 space-y-6 bg-muted/10">
      <div className="space-y-4">
        <h4
          ref={headingRef}
          tabIndex={-1}
          className="flex items-center gap-2 text-sm font-semibold outline-none"
        >
          {result.feedback.verdict === "correct" && (
            <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden="true" />
          )}
          {result.feedback.verdict === "incorrect" && (
            <XCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          )}
          {result.feedback.verdict === "partial" && (
            <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
          )}
          <span>{verdictWord}</span>
        </h4>

        <div className="text-sm">
          <span className="font-medium text-muted-foreground mr-1">
            Correct answer:
          </span>
          <MarkdownMath inline content={result.feedback.correct_answer} />
        </div>

        {result.feedback.misconception && (
          <div className="rounded border p-3 bg-muted/20 text-xs space-y-1">
            <span className="font-semibold text-foreground">Common mistake:</span>
            <div className="leading-relaxed">
              <MarkdownMath content={result.feedback.misconception} />
            </div>
          </div>
        )}

        <div className="space-y-1">
          <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Explanation
          </span>
          <div className="text-sm leading-relaxed">
            <MarkdownMath content={result.feedback.explanation} />
          </div>
        </div>

        {result.feedback.citations && result.feedback.citations.length > 0 && (
          <div className="space-y-2 pt-2 border-t">
            <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
              From your sources:
            </span>
            <div className="flex flex-wrap gap-1.5 pt-1">
              {result.feedback.citations.map((c, idx) => (
                <CitationChip
                  key={c.chunk_id || `${c.label}-${idx}`}
                  citation={c}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {finishError && (
        <div className="rounded-lg border p-4 space-y-3 bg-muted/20">
          <p className="text-sm text-muted-foreground">{finishError.message}</p>
          <div className="flex items-center gap-2">
            {finishError.retryable && onRetryFinish && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onRetryFinish}
                disabled={isFinishing}
              >
                Try again
              </Button>
            )}
            {finishError.code === "not_found" && onBackToTopics && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onBackToTopics}
              >
                Back to topics
              </Button>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center gap-3 pt-2">
        <Button
          type="button"
          onClick={onNext}
          disabled={isFinishing}
        >
          {isLastQuestion
            ? isFinishing
              ? "Finishing…"
              : "See results"
            : "Next question"}
        </Button>
      </div>
    </div>
  );
}
