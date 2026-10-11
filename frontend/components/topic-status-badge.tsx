"use client";

import Link from "next/link";
import type { TopicProgressItem } from "@/lib/api";

export interface TopicStatusBadgeProps {
  /** Whether Study Coach is enabled for this user/notebook */
  studyCoachOn: boolean;
  /** Authoritative progress record for this topic from progressMap */
  topicProgress: TopicProgressItem | undefined;
}

export function TopicStatusBadge({
  studyCoachOn,
  topicProgress,
}: TopicStatusBadgeProps) {
  if (topicProgress === undefined) {
    return <span className="text-xs text-muted-foreground">Progress unavailable</span>;
  }

  if (!studyCoachOn) {
    if (topicProgress.checked) {
      return <span className="text-xs text-muted-foreground">Studied</span>;
    }
    return (
      <span className="text-xs text-muted-foreground">
        Not checked yet ·{" "}
        <Link href="/settings" className="underline hover:text-foreground">
          Turn on Study Coach in Settings to track mastery
        </Link>
      </span>
    );
  }

  // When Study Coach is ON
  if (topicProgress.mastered === true) {
    return (
      <span className="text-xs font-medium border px-1.5 py-0.5 rounded bg-muted/30">
        Mastered
      </span>
    );
  }

  if (topicProgress.n_obs === 0) {
    return <span className="text-xs text-muted-foreground">Not started</span>;
  }

  if (typeof topicProgress.p_known === "number") {
    return (
      <span className="text-xs text-muted-foreground">
        Learning · {Math.round(topicProgress.p_known * 100)}%
      </span>
    );
  }

  return <span className="text-xs text-muted-foreground">Learning</span>;
}
