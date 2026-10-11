"use client";

import type { NormalisedQuestion, NormalisedFeedback } from "@/lib/api";
import { MarkdownMath } from "@/components/markdown-math";
import { CitationChip } from "@/components/citation-chip";

export interface QuizReviewItem {
  questionNumber: number;
  totalQuestions: number;
  question: NormalisedQuestion;
  studentAnswer: string;
  feedback: NormalisedFeedback;
}

export interface QuizReviewProps {
  items: QuizReviewItem[];
}

function formatVerdictWord(verdict: NormalisedFeedback["verdict"]): string {
  switch (verdict) {
    case "correct":
      return "Correct";
    case "incorrect":
      return "Incorrect";
    case "partial":
      return "Partly correct";
    default: {
      const _exhaustive: never = verdict;
      return _exhaustive;
    }
  }
}

export function QuizReview({ items }: QuizReviewProps) {
  if (!items || items.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4 pt-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        Review your answers
      </h3>

      <div className="space-y-4">
        {items.map((item) => {
          const verdictWord = formatVerdictWord(item.feedback.verdict);

          return (
            <div
              key={item.question.id}
              className="rounded-lg border p-4 sm:p-6 space-y-4 max-w-full break-words"
            >
              <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Question {item.questionNumber} of {item.totalQuestions}
                </span>
                <span className="text-sm font-medium text-foreground">
                  {verdictWord}
                </span>
              </div>

              <div className="text-sm leading-relaxed break-words">
                <MarkdownMath content={item.question.stem} />
              </div>

              <div className="space-y-2 text-sm break-words">
                <div>
                  <span className="font-medium text-muted-foreground mr-1">
                    Your answer:
                  </span>
                  <MarkdownMath inline content={item.studentAnswer} />
                </div>

                <div>
                  <span className="font-medium text-muted-foreground mr-1">
                    Correct answer:
                  </span>
                  <MarkdownMath inline content={item.feedback.correct_answer} />
                </div>
              </div>

              {item.feedback.misconception && (
                <div className="rounded border p-3 bg-muted/20 text-xs space-y-1 break-words">
                  <span className="font-semibold text-foreground">Common mistake:</span>
                  <div className="leading-relaxed">
                    <MarkdownMath content={item.feedback.misconception} />
                  </div>
                </div>
              )}

              <div className="space-y-1">
                <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Explanation
                </span>
                <div className="text-sm leading-relaxed break-words">
                  <MarkdownMath content={item.feedback.explanation} />
                </div>
              </div>

              {item.feedback.citations && item.feedback.citations.length > 0 && (
                <div className="space-y-2 pt-2 border-t">
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                    From your sources:
                  </span>
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {item.feedback.citations.map((c, idx) => (
                      <CitationChip
                        key={c.chunk_id || `${c.label}-${idx}`}
                        citation={c}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
