"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useRequireUser } from "@/lib/use-require-user";
import { AccountBar } from "@/components/account-bar";
import { StudyCoachPrompt } from "@/components/study-coach-prompt";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, createNotebook, listNotebooks } from "@/lib/api";

export default function NotebooksPage() {
  const { user, loading } = useRequireUser();
  const router = useRouter();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");

  const createMutation = useMutation({
    mutationFn: (trimmedName: string) => createNotebook(trimmedName),
    onSuccess: async (newNotebook) => {
      await queryClient.invalidateQueries({ queryKey: ["notebooks", user?.uid] });
      setName("");
      router.push(`/notebooks/${encodeURIComponent(newNotebook.id)}`);
    },
  });

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || createMutation.isPending) {
      return;
    }
    createMutation.mutate(trimmed);
  };

  const {
    data,
    isLoading,
    isError,
    error,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ["notebooks", user?.uid],
    queryFn: ({ pageParam }) => listNotebooks(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !loading && !!user,
  });

  if (loading) {
    return (
      <main className="p-8">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  if (!user) {
    return null;
  }

  const notebooks = data?.pages.flatMap((page) => page.items) ?? [];
  const isFirstPageLoaded = !isLoading && !isError && !!data;
  const isAllDemoOrEmpty = notebooks.every((nb) => nb.is_demo);
  const showEmptyState = isFirstPageLoaded && !hasNextPage && isAllDemoOrEmpty;

  return (
    <main className="p-8 space-y-8 max-w-4xl mx-auto">
      <div className="space-y-4">
        <h1 className="text-2xl font-bold tracking-tight">Notebooks</h1>
        <AccountBar />
        <StudyCoachPrompt />
      </div>

      <section className="rounded-lg border p-6 space-y-4">
        <h2 className="text-lg font-semibold">New notebook</h2>
        <form onSubmit={handleCreate} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="notebook-name">Notebook name</Label>
            <Input
              id="notebook-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Probability test"
              disabled={createMutation.isPending}
              required
            />
          </div>
          <Button type="submit" disabled={!name.trim() || createMutation.isPending}>
            {createMutation.isPending ? "Creating…" : "Create notebook"}
          </Button>
          {createMutation.error && (
            <p className="text-sm text-muted-foreground">
              {createMutation.error instanceof ApiError
                ? createMutation.error.message
                : "Failed to create notebook"}
            </p>
          )}
        </form>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Your notebooks</h2>

        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

        {isError && (
          <p className="text-sm text-muted-foreground">
            {error instanceof ApiError ? error.message : "Failed to load notebooks."}
          </p>
        )}

        {showEmptyState && notebooks.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No notebooks yet. Create one to upload a PDF and start asking questions.
          </p>
        )}

        {notebooks.length > 0 && (
          <ul className="divide-y rounded-lg border">
            {notebooks.map((nb) => (
              <li key={nb.id} className="p-4 hover:bg-muted/50 transition-colors">
                <Link
                  href={`/notebooks/${encodeURIComponent(nb.id)}`}
                  className="flex items-center justify-between"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-foreground">{nb.name}</span>
                    {nb.is_demo && (
                      <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold text-secondary-foreground">
                        Demo
                      </span>
                    )}
                  </div>
                  <span className="text-sm text-muted-foreground capitalize">{nb.status}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}

        {showEmptyState && notebooks.length > 0 && (
          <p className="text-sm text-muted-foreground">
            You haven&apos;t created a notebook yet. Open the demo course to try it, or create a notebook to upload your own PDFs.
          </p>
        )}

        {hasNextPage && (
          <Button
            variant="outline"
            onClick={() => fetchNextPage()}
            disabled={isFetchingNextPage}
          >
            {isFetchingNextPage ? "Loading more…" : "Load more"}
          </Button>
        )}
      </section>
    </main>
  );
}
