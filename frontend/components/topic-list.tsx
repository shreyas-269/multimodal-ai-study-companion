"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listTopicSources, type TopicListItem } from "@/lib/api";
import { useTopics } from "@/lib/use-topics";
import { CitationChip, type Citation } from "@/components/citation-chip";
import { Button } from "@/components/ui/button";

export interface TopicListProps {
  notebookId: string;
  uid: string;
  onQuiz?: (topicId: string) => void;
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
    <li className="space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-1.5 min-w-0">
        <div className="min-w-0 flex-1">
          <span className="text-sm font-medium min-w-0 break-words">
            {topic.is_other ? topic.name : `${topic.order} · ${topic.name}`}
          </span>
          {!topic.is_other && prereqNames.length > 0 && (
            <p className="text-xs text-muted-foreground min-w-0 break-words">
              Builds on: {prereqNames}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
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
      </div>

      {isExpanded && (
        <div id={`topic-sources-${topic.id}`} className="space-y-2">
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

export function TopicList({ notebookId, uid, onQuiz }: TopicListProps) {
  const [expandedTopicId, setExpandedTopicId] = useState<string | null>(null);
  const [showAllSources, setShowAllSources] = useState(false);

  const topicsQuery = useTopics(notebookId, uid);

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

  const items = topicsQuery.data?.items ?? [];
  const courseTopics = items.filter((t) => !t.is_other);
  courseTopics.sort((a, b) => a.order - b.order);
  const otherTopics = items.filter((t) => t.is_other && t.location_count > 0);

  if (courseTopics.length === 0 && otherTopics.length === 0) {
    return null;
  }

  const visibleTopics = [...courseTopics, ...otherTopics];

  const topicNameMap = new Map<string, string>();
  for (const item of items) {
    topicNameMap.set(item.id, item.name);
  }

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">Topics</h2>
      </div>

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
          />
        ))}
      </ul>
    </section>
  );
}
