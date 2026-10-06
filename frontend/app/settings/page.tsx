"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useRequireUser } from "@/lib/use-require-user";
import { AccountBar } from "@/components/account-bar";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, getMe } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { usePatchMe } from "@/lib/use-patch-me";

interface CustomInstructionsFormProps {
  format?: components["schemas"]["UserFormat"] | null;
}

function CustomInstructionsForm({ format }: CustomInstructionsFormProps) {
  const [text, setText] = useState(format?.custom_instructions ?? "");
  const [savedValue, setSavedValue] = useState(format?.custom_instructions ?? "");
  const [isSaved, setIsSaved] = useState(false);

  const patchMutation = usePatchMe();

  const trimmed = text.trim();
  const isUnchanged = trimmed === savedValue;
  const isPending = patchMutation.isPending;

  const handleSave = () => {
    if (isPending || isUnchanged) {
      return;
    }
    const customInstructions = trimmed.length > 0 ? trimmed : null;
    patchMutation.mutate(
      {
        format: {
          custom_instructions: customInstructions,
        },
      },
      {
        onSuccess: (returnedUser) => {
          const returnedValue = returnedUser.format?.custom_instructions ?? "";
          setText(returnedValue);
          setSavedValue(returnedValue);
          setIsSaved(true);
        },
      }
    );
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    setIsSaved(false);
  };

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="custom-instructions">How should answers be written?</Label>
          <span className="text-xs text-muted-foreground">{text.length} / 2000</span>
        </div>
        <Textarea
          id="custom-instructions"
          rows={5}
          maxLength={2000}
          value={text}
          onChange={handleChange}
          disabled={isPending}
        />
        <p className="text-sm text-muted-foreground">
          These instructions are added to every answer the tutor writes for you. Example: Explain the intuition before the formula, and keep worked examples short.
        </p>
      </div>

      <div className="flex items-center gap-3">
        <Button
          type="button"
          onClick={handleSave}
          disabled={isPending || isUnchanged}
        >
          {isPending ? "Saving…" : "Save"}
        </Button>
        {isSaved && (
          <span className="text-sm text-muted-foreground" aria-live="polite">
            Saved.
          </span>
        )}
      </div>

      {patchMutation.error && (
        <p className="text-sm text-muted-foreground" role="alert">
          {patchMutation.error instanceof ApiError
            ? patchMutation.error.message
            : "Could not save your instructions. Try again."}
        </p>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { user, loading: authLoading } = useRequireUser();
  const coachMutation = usePatchMe();

  const {
    data: me,
    isLoading: meLoading,
    error: meError,
  } = useQuery({
    queryKey: ["me", user?.uid],
    queryFn: getMe,
    enabled: !authLoading && !!user,
  });

  if (authLoading || meLoading) {
    return (
      <main className="p-8 max-w-4xl mx-auto space-y-8">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  if (!user) {
    return null;
  }

  if (meError) {
    return (
      <main className="p-8 max-w-4xl mx-auto space-y-8">
        <div className="space-y-4">
          <Link
            href="/notebooks"
            className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
          >
            ← Back to notebooks
          </Link>
          <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        </div>
        <p className="text-sm text-muted-foreground">
          {meError instanceof ApiError ? meError.message : "Failed to load user profile"}
        </p>
      </main>
    );
  }

  if (!me) {
    return (
      <main className="p-8 max-w-4xl mx-auto space-y-8">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  const isCoachOn = me.study_coach === true;

  const getStatusLabel = (studyCoach: boolean | null | undefined): string => {
    if (studyCoach === true) return "Status: On";
    if (studyCoach === false) return "Status: Off";
    return "Status: Not chosen yet";
  };

  const handleToggleCoach = () => {
    coachMutation.mutate({ study_coach: !isCoachOn });
  };

  return (
    <main className="p-8 space-y-8 max-w-4xl mx-auto">
      <div className="space-y-4">
        <div>
          <Link
            href="/notebooks"
            className="text-sm text-muted-foreground hover:text-foreground inline-flex items-center gap-1"
          >
            ← Back to notebooks
          </Link>
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <AccountBar />
      </div>

      <section className="rounded-lg border p-6 space-y-4">
        <h2 className="text-lg font-semibold">Study Coach</h2>
        <p className="text-sm text-muted-foreground">
          Study Coach tracks how well you know each topic as you study, using your quiz answers, the questions you ask and the topics you tick as done. It points out your weak areas, picks quiz questions that target them, and gives you a review after every quiz. Only you can see it.
        </p>
        <p className="text-sm" aria-live="polite">
          {getStatusLabel(me.study_coach)}
        </p>
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={coachMutation.isPending}
            onClick={handleToggleCoach}
          >
            {isCoachOn ? "Turn off" : "Turn on"}
          </Button>
        </div>
        {coachMutation.error && (
          <p className="text-sm text-muted-foreground" role="alert">
            {coachMutation.error instanceof ApiError
              ? coachMutation.error.message
              : "Could not save your choice. Try again."}
          </p>
        )}
      </section>

      <section className="rounded-lg border p-6 space-y-4">
        <h2 className="text-lg font-semibold">Custom instructions</h2>
        <CustomInstructionsForm key={user.uid} format={me.format} />
      </section>
    </main>
  );
}
