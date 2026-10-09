"use client";

import type {
  NormalisedQuestion,
  QuizSummary,
  NormalisedFeedback,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { MarkdownMath } from "@/components/markdown-math";
import type { QuestionResult } from "./quiz-types";

export interface QuizReviewProps {
  summary: QuizSummary;
  questions: NormalisedQuestion[];
  results: Record<string, QuestionResult>;
  topicNameMap: Map<string, string>;
  onNewQuizSameTopics: () => void;
  onChooseOtherTopics: () => void;
}

function formatVerdictWord(verdict?: NormalisedFeedback["verdict"]): string {
  switch (verdict) {
    case "correct":
      return "Correct";
    case "incorrect":
      return "Not quite";
    case "partial":
      return "Partly right";
    default:
      return "Unanswered";
  }
}

export function QuizReview({
  summary,
  questions,
  results,
  topicNameMap,
  onNewQuizSameTopics,
  onChooseOtherTopics,
}: QuizReviewProps) {
  // L8: hide rows with total === 0
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

      <div className="space-y-4 pt-2">
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
          Question review
        </h3>

        <div className="space-y-4 divide-y">
          {questions.map((q, idx) => {
            const res = results[q.id];
            let yourAnswerText = res ? res.answer : "No answer";
            if (res && q.type === "mcq") {
              const matchedOpt = q.options.find((o) => o.id === res.answer);
              yourAnswerText = matchedOpt ? matchedOpt.text : res.answer;
            }

            const verdictWord = formatVerdictWord(res?.feedback?.verdict);
            const correctAnswerText = res?.feedback?.correct_answer ?? "N/A";

            return (
              <div
                key={q.id}
                className={idx > 0 ? "pt-4 space-y-2" : "space-y-2"}
              >
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>Question {idx + 1}</span>
                  <span className="font-medium text-foreground">{verdictWord}</span>
                </div>

                <div className="text-sm leading-relaxed">
                  <MarkdownMath content={q.stem} />
                </div>

                <div className="text-xs space-y-1 bg-muted/20 p-2.5 rounded">
                  <p>
                    <span className="font-semibold text-muted-foreground mr-1.5">
                      Your answer:
                    </span>
                    <MarkdownMath inline content={yourAnswerText} />
                  </p>
                  <p>
                    <span className="font-semibold text-muted-foreground mr-1.5">
                      Correct answer:
                    </span>
                    <MarkdownMath inline content={correctAnswerText} />
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </div>

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
