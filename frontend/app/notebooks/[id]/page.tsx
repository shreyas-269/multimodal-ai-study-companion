"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRequireUser } from "@/lib/use-require-user";
import { AccountBar } from "@/components/account-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, getNotebook, listSources, uploadSource } from "@/lib/api";

export default function NotebookDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  const { user, loading } = useRequireUser();
  const queryClient = useQueryClient();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const notebookQuery = useQuery({
    queryKey: ["notebook", user?.uid, id],
    queryFn: () => getNotebook(id),
    enabled: !loading && !!user && !!id,
  });

  const sourcesQuery = useInfiniteQuery({
    queryKey: ["sources", user?.uid, id],
    queryFn: ({ pageParam }) => listSources(id, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !loading && !!user && !!notebookQuery.data,
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadSource(id, file),
    onSettled: () => {
      if (user) {
        queryClient.invalidateQueries({ queryKey: ["sources", user.uid, id] });
        queryClient.invalidateQueries({ queryKey: ["notebook", user.uid, id] });
        queryClient.invalidateQueries({ queryKey: ["notebooks", user.uid] });
      }
    },
    onSuccess: (data) => {
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      setSelectedFile(null);
      if (data.status === "ready") {
        setUploadMessage(`${data.title} is ready.`);
      } else if (data.status === "failed") {
        setUploadMessage(`${data.title} failed: ${data.error ?? "unknown error"}`);
      } else {
        setUploadMessage(`${data.title} is processing.`);
      }
    },
    onError: (err) => {
      setUploadMessage(err instanceof ApiError ? err.message : "Failed to upload file.");
    },
  });

  if (loading || notebookQuery.isLoading) {
    return (
      <main className="p-8 space-y-6 max-w-4xl mx-auto">
        <AccountBar />
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  if (!user) {
    return null;
  }

  if (notebookQuery.isError) {
    const isNotFound =
      notebookQuery.error instanceof ApiError && notebookQuery.error.code === "not_found";

    return (
      <main className="p-8 space-y-6 max-w-4xl mx-auto">
        <AccountBar />
        <div>
          <Link href="/notebooks" className="text-sm text-muted-foreground hover:underline">
            ← Back to notebooks
          </Link>
        </div>
        <p className="text-sm text-muted-foreground">
          {isNotFound ? "Notebook not found." : notebookQuery.error.message}
        </p>
      </main>
    );
  }

  const notebook = notebookQuery.data;
  if (!notebook) {
    return null;
  }

  const isOwner = notebook.owner_uid === user.uid && !notebook.is_demo;

  const handleUploadSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setUploadMessage(null);

    const file = fileInputRef.current?.files?.[0];
    if (!file) {
      setUploadMessage("Choose a PDF first.");
      return;
    }
    if (!selectedFile) {
      setSelectedFile(file);
    }

    const isPdf =
      file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setUploadMessage("Only PDF files are supported for now.");
      return;
    }

    if (file.size > 30 * 1024 * 1024) {
      setUploadMessage("File is larger than 30 MB.");
      return;
    }

    uploadMutation.mutate(file);
  };

  const sources = sourcesQuery.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <main className="p-8 space-y-8 max-w-4xl mx-auto">
      <div className="space-y-4">
        <div>
          <Link href="/notebooks" className="text-sm text-muted-foreground hover:underline">
            ← Back to notebooks
          </Link>
        </div>
        <AccountBar />
      </div>

      <div className="space-y-1">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight">{notebook.name}</h1>
          {notebook.is_demo && (
            <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold text-secondary-foreground">
              Demo
            </span>
          )}
        </div>
        <p className="text-sm text-muted-foreground capitalize">Status: {notebook.status}</p>
      </div>

      <section className="rounded-lg border p-6 space-y-4">
        <h2 className="text-lg font-semibold">Upload source</h2>
        {!isOwner ? (
          <p className="text-sm text-muted-foreground">Only this notebook&apos;s owner can add sources.</p>
        ) : (
          <form onSubmit={handleUploadSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="source-file">PDF file (max 30 MB)</Label>
              <Input
                id="source-file"
                ref={fileInputRef}
                type="file"
                accept=".pdf,application/pdf"
                disabled={uploadMutation.isPending}
                onChange={(e) => {
                  setUploadMessage(null);
                  setSelectedFile(e.target.files?.[0] ?? null);
                }}
              />
            </div>

            <Button type="submit" disabled={uploadMutation.isPending}>
              {uploadMutation.isPending ? "Uploading…" : "Upload PDF"}
            </Button>

            {uploadMutation.isPending && (
              <p className="text-sm text-muted-foreground">
                Processing {selectedFile?.name ?? "file"}… this can take up to 2 minutes. Keep this page open.
              </p>
            )}

            {!uploadMutation.isPending && uploadMessage && (
              <p className="text-sm text-muted-foreground">{uploadMessage}</p>
            )}
          </form>
        )}
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold">Sources</h2>

        {sourcesQuery.isPending && (
          <p className="text-sm text-muted-foreground">Loading sources…</p>
        )}

        {sourcesQuery.isError && (
          <p className="text-sm text-muted-foreground">
            {sourcesQuery.error instanceof ApiError
              ? sourcesQuery.error.message
              : "Failed to load sources."}
          </p>
        )}

        {!sourcesQuery.isPending && !sourcesQuery.isError && sources.length === 0 && (
          <p className="text-sm text-muted-foreground">No sources yet.</p>
        )}

        {sources.length > 0 && (
          <ul className="divide-y rounded-lg border">
            {sources.map((src) => (
              <li key={src.id} className="p-4 space-y-1">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{src.title}</span>
                  <span className="text-sm text-muted-foreground capitalize">{src.status}</span>
                </div>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  {src.page_count != null && <span>{src.page_count} pages</span>}
                  {src.error && <span className="text-destructive">{src.error}</span>}
                </div>
              </li>
            ))}
          </ul>
        )}

        {sourcesQuery.hasNextPage && (
          <Button
            variant="outline"
            onClick={() => sourcesQuery.fetchNextPage()}
            disabled={sourcesQuery.isFetchingNextPage}
          >
            {sourcesQuery.isFetchingNextPage ? "Loading more sources…" : "Load more sources"}
          </Button>
        )}
      </section>

      <section className="pt-4 border-t">
        <p className="text-sm text-muted-foreground">Asking questions arrives in the next task.</p>
      </section>
    </main>
  );
}
