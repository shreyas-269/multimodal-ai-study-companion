import { useQuery } from "@tanstack/react-query";
import { listTopics, type TopicListResponse } from "@/lib/api";

export function useTopics(notebookId: string, uid: string) {
  return useQuery<TopicListResponse>({
    queryKey: ["topics", uid, notebookId],
    queryFn: () => listTopics(notebookId),
    staleTime: Infinity,
  });
}
