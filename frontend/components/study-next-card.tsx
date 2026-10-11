"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import type {
  NeedsWorkItem,
  NeedsWorkResponse,
  TopicListItem,
  TopicProgressItem,
} from "@/lib/api";
import { Button } from "@/components/ui/button";

export interface StudyNextCardProps {
  /** The needs-work query result, containing data, loading, error, and refetch */
  needsWorkQuery: UseQueryResult<NeedsWorkResponse, unknown>;
  /** Course topics (!t.is_other) for name and syllabus resolution */
  courseTopics: TopicListItem[];
  /** Progress map for looking up n_obs observation counts */
  progressMap: Map<string, TopicProgressItem>;
  /** Preselect topic and switch to quiz */
  onQuiz?: (topicId: string) => void;
  /** Preselect topic and switch to chat */
  onChat?: (topicId: string) => void;
  /** Whether chat is currently streaming/busy */
  chatDisabled?: boolean;
}

function formatReason(
  item: NeedsWorkItem,
  progressItem: TopicProgressItem | undefined,
  studyCoach: boolean
): string | null {
  if (!studyCoach) {
    const base = "Not studied yet";
    if (item.chat_signals && item.chat_signals > 0) {
      const times = item.chat_signals === 1 ? "1 time" : `${item.chat_signals} times`;
      return `${base} · Asked about ${times} in chat`;
    }
    return base;
  }

  // When studyCoach is true
  const parts: string[] = [];
  if (progressItem?.n_obs === 0) {
    parts.push("Not started yet");
  } else if (typeof item.p_known === "number") {
    parts.push(`Knowledge estimate: ${Math.round(item.p_known * 100)}%`);
  }

  if (item.chat_signals && item.chat_signals > 0) {
    const times = item.chat_signals === 1 ? "1 time" : `${item.chat_signals} times`;
    parts.push(`Asked about ${times} in chat`);
  }

  if (parts.length === 0) {
    return null;
  }
  return parts.join(" · ");
}

export function StudyNextCard({
  needsWorkQuery,
  courseTopics,
  progressMap,
  onQuiz,
  onChat,
  chatDisabled,
}: StudyNextCardProps) {
  // Gated on needsWorkQuery.data so retained recommendations remain visible on refetch errors
  if (!needsWorkQuery.data) {
    if (needsWorkQuery.isPending) {
      return (
        <div className="rounded-lg border p-4 space-y-3">
          <div className="h-4 w-32 bg-muted rounded animate-pulse" />
          <div className="h-10 bg-muted/50 rounded animate-pulse" />
        </div>
      );
    }
    if (needsWorkQuery.isError) {
      return (
        <div className="rounded-lg border p-4 space-y-2">
          <p className="text-xs text-muted-foreground">Couldn&apos;t load recommendations.</p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={() => needsWorkQuery.refetch()}
          >
            Try again
          </Button>
        </div>
      );
    }
    return null;
  }

  const studyCoach = needsWorkQuery.data.study_coach;
  const rawItems = needsWorkQuery.data.items;

  // Genuine All-Caught-Up State (strictly items.length === 0)
  if (rawItems.length === 0) {
    return (
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Study next</h3>
        <p className="text-xs text-muted-foreground mt-1">
          {studyCoach ? "All caught up! All topics mastered." : "All topics marked as studied."}
        </p>
      </div>
    );
  }

  // Resolve items against course topics
  const resolvedItems: Array<{ item: NeedsWorkItem; topic: TopicListItem }> = [];
  for (const item of rawItems) {
    const topic = courseTopics.find((t) => t.id === item.topic_id);
    if (topic) {
      resolvedItems.push({ item, topic });
    }
  }

  // Unresolved Topic IDs (rawItems.length > 0 but none resolved)
  if (resolvedItems.length === 0) {
    return (
      <div className="rounded-lg border p-4">
        <h3 className="text-sm font-semibold">Study next</h3>
        <p className="text-xs text-muted-foreground mt-1">
          Recommendations currently unavailable for these topics.
        </p>
      </div>
    );
  }

  // Display up to 3 valid resolved recommendations
  const displayedItems = resolvedItems.slice(0, 3);

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Study next</h3>
      </div>
      <ul className="space-y-2.5">
        {displayedItems.map(({ item, topic }) => {
          const progressItem = progressMap.get(item.topic_id);
          const reason = formatReason(item, progressItem, studyCoach);

          return (
            <li
              key={item.topic_id}
              className="flex flex-wrap items-center justify-between gap-2 p-2 rounded-md bg-muted/30 border border-muted"
            >
              <div className="min-w-0 flex-1">
                <p className="text-xs font-medium truncate">{topic.name}</p>
                {reason && (
                  <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{reason}</p>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {onChat && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => onChat(item.topic_id)}
                    disabled={chatDisabled}
                    aria-label={`Chat about ${topic.name}`}
                  >
                    Chat
                  </Button>
                )}
                {onQuiz && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={() => onQuiz(item.topic_id)}
                    aria-label={`Quiz on ${topic.name}`}
                  >
                    Quiz
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
