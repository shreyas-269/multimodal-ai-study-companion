"use client";

import React from "react";
import type { NormalisedQuestion } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownMath } from "@/components/markdown-math";
import type { QuizErrorState } from "./quiz-types";

export interface QuizQuestionCardProps {
  question: NormalisedQuestion;
  questionNumber: number;
  totalQuestions: number;
  topicName: string;
  currentDraft: string;
  onDraftChange: (value: string) => void;
  onSubmit: () => void;
  onQuit: () => void;
  isSubmitting: boolean;
  isLocked: boolean;
  markers?: {
    correctOptionId: string | null;
    chosenOptionId: string | null;
  };
  submitError?: QuizErrorState | null;
  onRetry?: () => void;
  onSeeResultsFromFinishedError?: () => void;
  onBackToTopics?: () => void;
  headingRef?: React.RefObject<HTMLHeadingElement | null>;
}

function formatDifficulty(diff: number): string | null {
  switch (diff) {
    case 1:
      return "Easy";
    case 2:
      return "Medium";
    case 3:
      return "Hard";
    default:
      return null;
  }
}

export function QuizQuestionCard({
  question,
  questionNumber,
  totalQuestions,
  topicName,
  currentDraft,
  onDraftChange,
  onSubmit,
  onQuit,
  isSubmitting,
  isLocked,
  markers,
  submitError,
  onRetry,
  onSeeResultsFromFinishedError,
  onBackToTopics,
  headingRef,
}: QuizQuestionCardProps) {
  const difficultyLabel = formatDifficulty(question.difficulty);
  const isMcqInvalid =
    question.type === "mcq" && (!question.options || question.options.length < 2);
  const isUnknownType =
    question.type !== "mcq" &&
    question.type !== "numerical" &&
    question.type !== "short";

  const isNotFound = submitError?.code === "not_found";
  const isFinished =
    (submitError?.code === "invalid" &&
      submitError?.message === "This quiz is finished.") ||
    (submitError?.source === "finish" && !isNotFound);

  const canCheckAnswer =
    !isLocked &&
    !isSubmitting &&
    !isNotFound &&
    currentDraft.trim().length > 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (canCheckAnswer) {
      onSubmit();
    }
  };

  if (isMcqInvalid || isUnknownType) {
    return (
      <div className="rounded-lg border p-6 space-y-4">
        <p className="text-sm text-muted-foreground">
          This question can&apos;t be shown.
        </p>
        <Button type="button" variant="outline" size="sm" onClick={onQuit}>
          Quit quiz
        </Button>
      </div>
    );
  }

  return (
    <div key={question.id} className="rounded-lg border p-6 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b pb-3">
        <div className="space-y-0.5">
          <h3
            ref={headingRef}
            tabIndex={-1}
            className="text-xs font-semibold text-muted-foreground uppercase tracking-wider outline-none"
          >
            Question {questionNumber} of {totalQuestions}
          </h3>
          <p className="text-sm font-medium">{topicName || "This topic"}</p>
        </div>

        {difficultyLabel && (
          <span className="text-xs px-2 py-0.5 border rounded-full text-muted-foreground">
            {difficultyLabel}
          </span>
        )}
      </div>

      <div
        id={`stem-${question.id}`}
        className="text-sm leading-relaxed"
      >
        <MarkdownMath content={question.stem} />
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {question.type === "mcq" && (
          <div
            role="radiogroup"
            aria-labelledby={`stem-${question.id}`}
            className="space-y-2.5"
          >
            {question.options.map((opt) => {
              const isChecked =
                isLocked && markers
                  ? opt.id === markers.chosenOptionId
                  : currentDraft === opt.id;
              let suffixLabel: string | null = null;
              if (isLocked && markers) {
                const isCorrect = opt.id === markers.correctOptionId;
                const isChosen = opt.id === markers.chosenOptionId;
                if (isCorrect && isChosen) {
                  suffixLabel = " (Your answer, correct)";
                } else if (isCorrect) {
                  suffixLabel = " (Correct answer)";
                } else if (isChosen) {
                  suffixLabel = " (Your answer)";
                }
              }

              return (
                <label
                  key={opt.id}
                  className={`flex items-start gap-3 p-3 rounded-lg border text-sm transition-colors ${
                    isLocked
                      ? "cursor-default"
                      : "cursor-pointer hover:bg-muted/30"
                  } ${isChecked ? "bg-muted/40 font-medium" : ""}`}
                >
                  <input
                    type="radio"
                    name={`quiz-options-${question.id}`}
                    value={opt.id}
                    checked={isChecked}
                    disabled={isLocked || isSubmitting}
                    onChange={() => onDraftChange(opt.id)}
                    className="mt-0.5 h-4 w-4 shrink-0"
                  />
                  <span className="flex-1 min-w-0">
                    <MarkdownMath inline content={opt.text} />
                    {suffixLabel && (
                      <span className="text-xs text-muted-foreground ml-2 font-normal">
                        {suffixLabel}
                      </span>
                    )}
                  </span>
                </label>
              );
            })}
          </div>
        )}

        {question.type === "numerical" && (
          <div className="space-y-2">
            <Input
              type="text"
              maxLength={100}
              value={currentDraft}
              onChange={(e) => onDraftChange(e.target.value.slice(0, 100))}
              disabled={isLocked || isSubmitting}
              placeholder="Enter numerical answer"
              aria-label="Numerical answer"
            />
            <p className="text-xs text-muted-foreground">
              Type an exact answer (3/8, 0.375 or 37.5%) or a decimal with at least 3 significant figures.
            </p>
          </div>
        )}

        {question.type === "short" && (
          <div className="space-y-2">
            <Textarea
              rows={3}
              maxLength={100}
              value={currentDraft}
              onChange={(e) => onDraftChange(e.target.value.slice(0, 100))}
              disabled={isLocked || isSubmitting}
              placeholder="Type your answer (max 100 characters)"
              aria-label="Short answer"
            />
          </div>
        )}

        {submitError && (
          <div className="rounded-lg border p-4 space-y-3 bg-muted/20">
            <p className="text-sm text-muted-foreground">{submitError.message}</p>
            <div className="flex items-center gap-2">
              {submitError.retryable && onRetry && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={onRetry}
                  disabled={isSubmitting}
                >
                  Try again
                </Button>
              )}
              {isFinished && onSeeResultsFromFinishedError && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={onSeeResultsFromFinishedError}
                >
                  See results
                </Button>
              )}
              {isNotFound && onBackToTopics && (
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
          {!isLocked && (
            <Button
              type="submit"
              disabled={!canCheckAnswer}
            >
              {isSubmitting ? "Checking…" : "Check answer"}
            </Button>
          )}

          <Button
            type="button"
            variant="outline"
            onClick={onQuit}
          >
            Quit quiz
          </Button>
        </div>
      </form>
    </div>
  );
}
