"use client";

import type { TopicListItem } from "@/lib/api";
import { Button } from "@/components/ui/button";
import type { QuizErrorState } from "./quiz-types";

export interface QuizStartFormProps {
  topics: TopicListItem[];
  servedCountsByTopic: Map<string, number>;
  selectedTopicIds: string[];
  count: 5 | 10;
  onToggleTopic: (topicId: string) => void;
  onCountChange: (count: 5 | 10) => void;
  onStart: () => void;
  isCreating: boolean;
  createError?: QuizErrorState | null;
  onRetryCreate?: () => void;
  onBackToTopics?: () => void;
  emptyTopicNotice?: string | null;
}

export function QuizStartForm({
  topics,
  servedCountsByTopic,
  selectedTopicIds,
  count,
  onToggleTopic,
  onCountChange,
  onStart,
  isCreating,
  createError,
  onRetryCreate,
  onBackToTopics,
  emptyTopicNotice,
}: QuizStartFormProps) {
  // Course topics in syllabus order, and Other only if location_count > 0
  const courseTopics = topics.filter((t) => !t.is_other);
  courseTopics.sort((a, b) => a.order - b.order);
  const otherTopics = topics.filter((t) => t.is_other && t.location_count > 0);
  const visibleTopics = [...courseTopics, ...otherTopics];

  // Derived effective selection (only topics that have at least 1 verified mcq or numerical question)
  const effectiveSelectedIds = selectedTopicIds.filter(
    (id) => (servedCountsByTopic.get(id) ?? 0) > 0
  );

  const isBankEmpty =
    visibleTopics.length === 0 ||
    visibleTopics.every((t) => (servedCountsByTopic.get(t.id) ?? 0) === 0);

  // Total verified questions available across selected topics
  let availableQuestions = 0;
  for (const id of effectiveSelectedIds) {
    availableQuestions += servedCountsByTopic.get(id) ?? 0;
  }

  const isMaxTopicsSelected = effectiveSelectedIds.length >= 6;
  const canStart =
    effectiveSelectedIds.length >= 1 &&
    effectiveSelectedIds.length <= 6 &&
    !isCreating;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">Test your understanding</h2>
        <p className="text-xs text-muted-foreground">
          Choose topics to quiz yourself on verified questions grounded in your course materials.
        </p>
      </div>

      {createError && (
        <div className="rounded-lg border p-4 space-y-3 bg-muted/20">
          <p className="text-sm text-muted-foreground">{createError.message}</p>
          <div className="flex items-center gap-2">
            {createError.retryable && onRetryCreate && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={onRetryCreate}
                disabled={isCreating}
              >
                Try again
              </Button>
            )}
            {onBackToTopics && (
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

      {isBankEmpty ? (
        <div className="rounded-lg border p-4 bg-muted/20">
          <p className="text-sm text-muted-foreground">
            No quiz questions have been built for this notebook yet.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Topics (1 to 6)</h3>
              {isMaxTopicsSelected && (
                <span className="text-xs text-muted-foreground">
                  Choose up to 6 topics
                </span>
              )}
            </div>

            <ul className="space-y-2 border rounded-lg p-3">
              {visibleTopics.map((topic) => {
                const verifiedCount = servedCountsByTopic.get(topic.id) ?? 0;
                const hasQuestions = verifiedCount > 0;
                const isChecked = effectiveSelectedIds.includes(topic.id);
                const isDisabled =
                  !hasQuestions || (!isChecked && isMaxTopicsSelected);

                return (
                  <li key={topic.id} className="flex items-center justify-between py-1 px-1">
                    <label
                      htmlFor={`topic-checkbox-${topic.id}`}
                      className={`flex items-center gap-2.5 text-sm select-none ${
                        hasQuestions ? "cursor-pointer" : "cursor-not-allowed opacity-60"
                      }`}
                    >
                      <input
                        type="checkbox"
                        id={`topic-checkbox-${topic.id}`}
                        checked={isChecked}
                        disabled={isDisabled}
                        onChange={() => onToggleTopic(topic.id)}
                        className="h-4 w-4 rounded border-border"
                      />
                      <span>
                        {topic.is_other ? topic.name : `${topic.order} · ${topic.name}`}
                      </span>
                    </label>

                    <span className="text-xs text-muted-foreground shrink-0">
                      {hasQuestions
                        ? `${verifiedCount} ${verifiedCount === 1 ? "question" : "questions"}`
                        : "No questions yet"}
                    </span>
                  </li>
                );
              })}
            </ul>

            {emptyTopicNotice && (
              <p className="text-xs text-muted-foreground mt-2">
                {emptyTopicNotice}
              </p>
            )}
          </div>

          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold">Question count</legend>
            <div className="flex items-center gap-6">
              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input
                  type="radio"
                  name="quiz-count"
                  value="5"
                  checked={count === 5}
                  onChange={() => onCountChange(5)}
                  className="h-4 w-4"
                />
                <span>5 questions</span>
              </label>
              <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                <input
                  type="radio"
                  name="quiz-count"
                  value="10"
                  checked={count === 10}
                  onChange={() => onCountChange(10)}
                  className="h-4 w-4"
                />
                <span>10 questions</span>
              </label>
            </div>
            {effectiveSelectedIds.length > 0 && availableQuestions < count && (
              <p className="text-xs text-muted-foreground">
                Only {availableQuestions} {availableQuestions === 1 ? "question" : "questions"} available
              </p>
            )}
          </fieldset>

          <div className="pt-2">
            <Button
              type="button"
              disabled={!canStart}
              onClick={onStart}
            >
              {isCreating ? "Starting…" : "Start quiz"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
