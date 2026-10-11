"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import type { ProgressResponse, TopicListItem } from "@/lib/api";
import { Button } from "@/components/ui/button";

export interface ProgressSummaryProps {
  /** The progress query result, containing data, loading, error, and refetch */
  progressQuery: UseQueryResult<ProgressResponse, unknown>;
  /** Course topics (filtered !t.is_other), used for total denominator */
  courseTopics: TopicListItem[];
}

export function ProgressSummary({
  progressQuery,
  courseTopics,
}: ProgressSummaryProps) {
  const totalCourseTopics = courseTopics.length;

  if (totalCourseTopics === 0) {
    return null;
  }

  // Loading state (no data in cache yet)
  if (progressQuery.isPending && !progressQuery.data) {
    return (
      <div className="space-y-1.5">
        <div className="h-3 w-32 bg-muted rounded animate-pulse" />
        <div className="h-2 w-full rounded-full bg-muted animate-pulse" />
      </div>
    );
  }

  // Error state (no retained data in cache)
  if (progressQuery.isError && !progressQuery.data) {
    return (
      <div className="flex items-center justify-between text-xs text-muted-foreground p-2 rounded-md border border-muted">
        <span>Couldn&apos;t load progress.</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-6 px-2 text-xs"
          onClick={() => progressQuery.refetch()}
        >
          Try again
        </Button>
      </div>
    );
  }

  if (!progressQuery.data) {
    return null;
  }

  const studyCoach = progressQuery.data.study_coach;
  const items = progressQuery.data.items;

  let completedCount = 0;
  let label = "";

  if (studyCoach) {
    completedCount = items.filter((it) => it.mastered === true).length;
    const percent =
      totalCourseTopics > 0
        ? Math.round((completedCount / totalCourseTopics) * 100)
        : 0;
    label = `${completedCount} of ${totalCourseTopics} topics mastered (${percent}%)`;
  } else {
    completedCount = items.filter((it) => it.checked === true).length;
    const percent =
      totalCourseTopics > 0
        ? Math.round((completedCount / totalCourseTopics) * 100)
        : 0;
    label = `${completedCount} of ${totalCourseTopics} topics checked (${percent}%)`;
  }

  const percent =
    totalCourseTopics > 0
      ? Math.round((completedCount / totalCourseTopics) * 100)
      : 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{label}</span>
      </div>
      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Notebook progress"
        className="h-2 w-full rounded-full bg-muted overflow-hidden"
      >
        <div
          className="h-full bg-foreground transition-all duration-300"
          style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
        />
      </div>
    </div>
  );
}
