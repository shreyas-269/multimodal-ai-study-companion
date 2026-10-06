"use client";

import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/components/auth-provider";
import { ApiError, getMe } from "@/lib/api";
import { usePatchMe } from "@/lib/use-patch-me";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function StudyCoachPrompt() {
  const { user, loading } = useAuth();
  const patchMutation = usePatchMe();

  const { data, isLoading, error } = useQuery({
    queryKey: ["me", user?.uid],
    queryFn: getMe,
    enabled: !loading && !!user,
  });

  if (isLoading || error || !data || data.study_coach != null) {
    return null;
  }

  const handleChoice = (enabled: boolean) => {
    patchMutation.mutate({ study_coach: enabled });
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Want Study Coach?</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Study Coach tracks how well you know each topic as you study, using your quiz answers, the questions you ask and the topics you tick as done. It points out your weak areas, picks quiz questions that target them, and gives you a review after every quiz. Only you can see it.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="default"
            disabled={patchMutation.isPending}
            onClick={() => handleChoice(true)}
          >
            Yes, turn it on
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={patchMutation.isPending}
            onClick={() => handleChoice(false)}
          >
            No thanks
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          You can change this any time in Settings.
        </p>
        {patchMutation.error && (
          <p className="text-sm text-muted-foreground" role="alert">
            {patchMutation.error instanceof ApiError
              ? patchMutation.error.message
              : "Could not save your choice. Try again."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
