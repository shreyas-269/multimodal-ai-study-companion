"use client";

import type { QuizSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { QuizReview as AnswerReview, type QuizReviewItem } from "@/components/quiz-review";

export interface QuizReviewProps {
  summary: QuizSummary;
  topicNameMap: Map<string, string>;
  reviewItems?: QuizReviewItem[];
  onNewQuizSameTopics: () => void;
  onChooseOtherTopics: () => void;
}

export function QuizReview({
  summary,
  topicNameMap,
  reviewItems = [],
  onNewQuizSameTopics,
  onChooseOtherTopics,
}: QuizReviewProps) {
  // Hide rows with total === 0
  const activeTopicRows = summary.by_topic.filter((item) => item.total > 0);

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">Quiz results</h2>
        <p className="text-sm font-medium">
          {summary.correct} of {summary.total} correct
        </p>
      </div>

      {activeTopicRows.length > 0 && (
        <div className="space-y-2 border rounded-lg p-4">
          <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            By topic
          </h3>
          <ul className="space-y-1.5 text-sm">
            {activeTopicRows.map((item) => {
              const name = topicNameMap.get(item.topic_id) ?? "This topic";
              return (
                <li key={item.topic_id} className="flex justify-between items-center py-0.5">
                  <span>{name}</span>
                  <span className="text-muted-foreground">
                    {item.correct} of {item.total} correct
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Review your answers section rendered BELOW existing results summary */}
      <AnswerReview items={reviewItems} />

      <div className="flex flex-wrap items-center gap-3 pt-4 border-t">
        <Button type="button" onClick={onNewQuizSameTopics}>
          New quiz on these topics
        </Button>
        <Button type="button" variant="outline" onClick={onChooseOtherTopics}>
          Choose other topics
        </Button>
      </div>
    </div>
  );
}
