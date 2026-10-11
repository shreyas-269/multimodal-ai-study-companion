"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  getNeedsWork,
  getProgress,
  listTopicSources,
  putTopicChecked,
  type TopicListItem,
  type TopicProgressItem,
} from "@/lib/api";
import { useTopics } from "@/lib/use-topics";
import { CitationChip, type Citation } from "@/components/citation-chip";
import { Button } from "@/components/ui/button";
import { ProgressSummary } from "@/components/progress-summary";
import { StudyNextCard } from "@/components/study-next-card";
import { TopicStatusBadge } from "@/components/topic-status-badge";

export interface TopicListProps {
  notebookId: string;
  uid: string;
  onQuiz?: (topicId: string) => void;
  onChat?: (topicId: string) => void;
  chatDisabled?: boolean;
}

interface TopicItemRowProps {
  topic: TopicListItem;
  notebookId: string;
  uid: string;
  isExpanded: boolean;
  onToggleSources: () => void;
  showAllSources: boolean;
  onShowAllSources: () => void;
  topicNameMap: Map<string, string>;
  onQuiz?: (topicId: string) => void;
  onChat?: (topicId: string) => void;
  chatDisabled?: boolean;

  // --- F5 Additions ---
  studyCoachOn: boolean;
  topicProgress: TopicProgressItem | undefined;
  isInitialLoading: boolean;
  isPending: boolean;
  isAnyInFlight: boolean;
  mutationErrorMessage: string | null;
  onToggleChecked: (topicId: string, currentChecked: boolean) => void;
}

function TopicItemRow({
  topic,
  notebookId,
  uid,
  isExpanded,
  onToggleSources,
  showAllSources,
  onShowAllSources,
  topicNameMap,
  onQuiz,
  onChat,
  chatDisabled,
  studyCoachOn,
  topicProgress,
  isInitialLoading,
  isPending,
  isAnyInFlight,
  mutationErrorMessage,
  onToggleChecked,
}: TopicItemRowProps) {
  const sourcesQuery = useQuery({
    queryKey: ["topic-sources", uid, notebookId, topic.id],
    queryFn: () => listTopicSources(notebookId, topic.id),
    enabled: isExpanded && !!notebookId && !!uid,
    staleTime: Infinity,
  });

  const prereqNames = (topic.prerequisite_ids ?? [])
    .map((id) => topicNameMap.get(id))
    .filter((name): name is string => Boolean(name))
    .join(", ");

  const rawCitations = sourcesQuery.data ?? [];
  const seenLabels = new Set<string>();
  const uniqueCitations: Citation[] = [];
  for (const c of rawCitations) {
    if (!seenLabels.has(c.label)) {
      seenLabels.add(c.label);
      uniqueCitations.push(c);
    }
  }

  const displayedCitations = showAllSources
    ? uniqueCitations
    : uniqueCitations.slice(0, 20);

  return (
    <li className="space-y-1.5">
      {/* Row 1 (Header line): Checkbox and Topic Title */}
      <div className="flex items-start gap-2.5 w-full">
        {!topic.is_other && (
          <input
            type="checkbox"
            id={`topic-checkbox-${topic.id}`}
            checked={topicProgress?.checked ?? false}
            onChange={() => {
              if (topicProgress !== undefined) {
                onToggleChecked(topic.id, topicProgress.checked);
              }
            }}
            disabled={isPending || isAnyInFlight || topicProgress === undefined || isInitialLoading}
            aria-disabled={isInitialLoading || topicProgress === undefined || isPending || isAnyInFlight}
            aria-label={`Mark ${topic.name} as studied`}
            className="h-4 w-4 shrink-0 mt-0.5 rounded border-muted-foreground/30 accent-foreground cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
          />
        )}
        <span className="flex-1 min-w-0 text-sm font-medium leading-snug break-words">
          {topic.is_other ? topic.name : `${topic.order} · ${topic.name}`}
        </span>
      </div>

      {/* Row 2 (Status & Mastery line) */}
      {(!topic.is_other || mutationErrorMessage) && (
        <div className={`space-y-1 ${!topic.is_other ? "pl-6" : ""}`}>
          {!topic.is_other && (
            <div className="text-xs text-muted-foreground">
              <TopicStatusBadge
                studyCoachOn={studyCoachOn}
                topicProgress={topicProgress}
              />
            </div>
          )}
          {!topic.is_other && prereqNames.length > 0 && (
            <p className="text-xs text-muted-foreground min-w-0 break-words">
              Builds on: {prereqNames}
            </p>
          )}
          {mutationErrorMessage && (
            <p className="text-xs text-destructive">{mutationErrorMessage}</p>
          )}
        </div>
      )}

      {/* Row 3 (Action button group) */}
      <div className={`flex items-center gap-1.5 flex-wrap pt-1 ${!topic.is_other ? "pl-6" : ""}`}>
        {onChat && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onChat(topic.id)}
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
            className="h-7 px-2 text-xs"
            onClick={() => onQuiz(topic.id)}
            aria-label={`Quiz on ${topic.name}`}
          >
            Quiz
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={onToggleSources}
          aria-expanded={isExpanded}
          aria-controls={isExpanded ? `topic-sources-${topic.id}` : undefined}
        >
          Sources ({topic.location_count})
        </Button>
      </div>

      {isExpanded && (
        <div id={`topic-sources-${topic.id}`} className={`space-y-2 pt-1 ${!topic.is_other ? "pl-6" : ""}`}>
          {topic.summary?.trim() ? (
            <p className="text-xs text-muted-foreground min-w-0 break-words">
              {topic.summary.trim()}
            </p>
          ) : null}

          {sourcesQuery.isPending && (
            <p className="text-xs text-muted-foreground">Loading sources…</p>
          )}

          {sourcesQuery.isError && (
            <p className="text-xs text-muted-foreground">{"Couldn't load this topic's sources."}</p>
          )}

          {!sourcesQuery.isPending && !sourcesQuery.isError && (
            <>
              {uniqueCitations.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No passages are tagged to this topic yet.
                </p>
              ) : (
                <div className="space-y-2">
                  <div className="flex min-w-0 flex-wrap gap-1.5 [&>*]:max-w-full [&>*]:h-auto [&>*]:whitespace-normal [&>*]:text-left">
                    {displayedCitations.map((c, idx) => (
                      <CitationChip key={`${c.label}-${idx}`} citation={c} />
                    ))}
                  </div>

                  {!showAllSources && uniqueCitations.length > 20 && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={onShowAllSources}
                    >
                      Show all {uniqueCitations.length}
                    </Button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </li>
  );
}

const EMPTY_ITEMS: TopicListItem[] = [];

export function TopicList({ notebookId, uid, onQuiz, onChat, chatDisabled }: TopicListProps) {
  const [expandedTopicId, setExpandedTopicId] = useState<string | null>(null);
  const [showAllSources, setShowAllSources] = useState(false);
  const inFlightTopicRef = useRef<string | null>(null);
  const [inFlightTopicId, setInFlightTopicId] = useState<string | null>(null);
  const [mutationError, setMutationError] = useState<{ topicId: string; message: string } | null>(null);

  const queryClient = useQueryClient();
  const topicsQuery = useTopics(notebookId, uid);

  const items = topicsQuery.data?.items ?? EMPTY_ITEMS;
  const courseTopics = useMemo(
    () => items.filter((t) => !t.is_other).sort((a, b) => a.order - b.order),
    [items]
  );
  const otherTopics = useMemo(
    () => items.filter((t) => t.is_other && t.location_count > 0),
    [items]
  );
  const hasCourseTopics = courseTopics.length > 0;

  const progressQuery = useQuery({
    queryKey: ["progress", uid, notebookId],
    queryFn: () => getProgress(notebookId),
    enabled:
      !topicsQuery.isPending &&
      !topicsQuery.isError &&
      Boolean(notebookId) &&
      Boolean(uid) &&
      hasCourseTopics,
    retry: false,
  });

  const needsWorkQuery = useQuery({
    queryKey: ["needs-work", uid, notebookId],
    queryFn: () => getNeedsWork(notebookId, 3),
    enabled:
      !topicsQuery.isPending &&
      !topicsQuery.isError &&
      Boolean(notebookId) &&
      Boolean(uid) &&
      hasCourseTopics,
    retry: false,
  });

  const progressMap = useMemo(() => {
    const map = new Map<string, TopicProgressItem>();
    for (const it of progressQuery.data?.items ?? []) {
      map.set(it.topic_id, it);
    }
    return map;
  }, [progressQuery.data]);

  const topicNameMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const item of items) {
      map.set(item.id, item.name);
    }
    return map;
  }, [items]);

  const toggleMutation = useMutation({
    mutationFn: ({ topicId, checked }: { topicId: string; checked: boolean }) =>
      putTopicChecked(notebookId, topicId, checked),
    retry: false,
    onMutate: () => {
      setMutationError(null);
    },
    onSettled: async (_data, error, variables) => {
      let reconcileFailed = false;
      try {
        const results = await Promise.allSettled([
          queryClient.invalidateQueries(
            { queryKey: ["progress", uid, notebookId] },
            { throwOnError: true }
          ),
          queryClient.invalidateQueries(
            { queryKey: ["needs-work", uid, notebookId] },
            { throwOnError: true }
          ),
        ]);

        if (results.some((r) => r.status === "rejected")) {
          reconcileFailed = true;
        }

        const pState = queryClient.getQueryState(["progress", uid, notebookId]);
        const nwState = queryClient.getQueryState(["needs-work", uid, notebookId]);
        if (pState?.status === "error" || nwState?.status === "error") {
          reconcileFailed = true;
        }
      } catch {
        reconcileFailed = true;
      } finally {
        inFlightTopicRef.current = null;
        setInFlightTopicId(null);
      }

      if (error && variables) {
        const baseMsg =
          error instanceof ApiError
            ? error.message
            : error instanceof Error
              ? error.message
              : "Could not update";
        const separator = baseMsg.endsWith(".") ? " " : ". ";

        if (reconcileFailed) {
          setMutationError({
            topicId: variables.topicId,
            message: `${baseMsg}${separator}Could not refresh server state.`,
          });
        } else {
          setMutationError({
            topicId: variables.topicId,
            message: `${baseMsg}${separator}Latest server state refreshed.`,
          });
        }
      } else if (reconcileFailed && variables) {
        setMutationError({
          topicId: variables.topicId,
          message: "Saved, but failed to refresh progress. Try reloading.",
        });
      }
    },
  });

  const handleToggleChecked = (topicId: string, currentChecked: boolean) => {
    if (inFlightTopicRef.current !== null) return;
    if (!progressMap.has(topicId)) return;

    inFlightTopicRef.current = topicId;
    setInFlightTopicId(topicId);
    setMutationError(null);

    toggleMutation.mutate({ topicId, checked: !currentChecked });
  };

  const handleToggleSources = (topicId: string) => {
    setShowAllSources(false);
    setExpandedTopicId((current) => (current === topicId ? null : topicId));
  };

  if (topicsQuery.isPending) {
    return <p className="text-xs text-muted-foreground">Loading topics…</p>;
  }

  if (topicsQuery.isError) {
    return <p className="text-xs text-muted-foreground">{"Couldn't load topics."}</p>;
  }

  if (courseTopics.length === 0 && otherTopics.length === 0) {
    return null;
  }

  const visibleTopics = [...courseTopics, ...otherTopics];
  const studyCoachOn = progressQuery.data?.study_coach ?? true;

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Topics</h2>
      </div>

      {hasCourseTopics && (
        <>
          <ProgressSummary
            progressQuery={progressQuery}
            courseTopics={courseTopics}
          />
          <StudyNextCard
            needsWorkQuery={needsWorkQuery}
            courseTopics={courseTopics}
            progressMap={progressMap}
            onQuiz={onQuiz}
            onChat={onChat}
            chatDisabled={chatDisabled}
          />
        </>
      )}

      <ul className="space-y-3">
        {visibleTopics.map((topic) => (
          <TopicItemRow
            key={topic.id}
            topic={topic}
            notebookId={notebookId}
            uid={uid}
            isExpanded={expandedTopicId === topic.id}
            onToggleSources={() => handleToggleSources(topic.id)}
            showAllSources={showAllSources}
            onShowAllSources={() => setShowAllSources(true)}
            topicNameMap={topicNameMap}
            onQuiz={onQuiz}
            onChat={onChat}
            chatDisabled={chatDisabled}
            studyCoachOn={studyCoachOn}
            topicProgress={progressMap.get(topic.id)}
            isInitialLoading={progressQuery.isPending && !progressQuery.data}
            isPending={inFlightTopicId === topic.id}
            isAnyInFlight={inFlightTopicId !== null}
            mutationErrorMessage={
              mutationError?.topicId === topic.id ? mutationError.message : null
            }
            onToggleChecked={handleToggleChecked}
          />
        ))}
      </ul>
    </section>
  );
}
