"use client";

import { useRef, useState, useEffect } from "react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRequireUser } from "@/lib/use-require-user";
import { AccountBar } from "@/components/account-bar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { AnswerView } from "@/components/answer";
import {
  ApiError,
  getNotebook,
  listSources,
  uploadSource,
  ask,
  type AskRequest,
  type AskResponse,
} from "@/lib/api";
import { ViewerProvider, useViewer } from "@/components/viewer-context";

const PdfViewer = dynamic(
  () => import("@/components/pdf-viewer").then((mod) => mod.PdfViewer),
  {
    ssr: false,
    loading: () => <p className="text-sm text-muted-foreground p-4">Loading PDF viewer…</p>,
  }
);

interface AnswerItem {
  id: string;
  question: string;
  response: AskResponse;
}

function formatAskError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "not_ready") {
      return "This notebook has no processed sources yet.";
    }
    if (err.code === "quota_exhausted") {
      if (err.retryAfterS != null) {
        return `The AI model's free quota is used up. Try again in ${err.retryAfterS} seconds.`;
      }
      return "The AI model's free quota is used up. Try again later.";
    }
    if (err.code === "unavailable" || err.status === 503) {
      return "The AI model is busy. Try again in a minute.";
    }
    if (err.code === "timeout") {
      return err.message;
    }
    return err.message;
  }
  return "Failed to get an answer.";
}

export default function NotebookDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  return (
    <ViewerProvider>
      <NotebookContent key={id} />
    </ViewerProvider>
  );
}

function NotebookContent() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";

  const { user, loading } = useRequireUser();
  const queryClient = useQueryClient();
  const { viewerSource, openSource, setPage, closeViewer } = useViewer();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const viewerRef = useRef<HTMLElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [allowOutside, setAllowOutside] = useState(false);
  const [answers, setAnswers] = useState<AnswerItem[]>([]);

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

  const askMutation = useMutation({
    mutationFn: (data: AskRequest) => ask(id, data),
    retry: false,
    onSuccess: (response, variables) => {
      setAnswers((prev) => [
        {
          id: `${Date.now()}-${Math.random()}`,
          question: variables.question,
          response,
        },
        ...prev,
      ]);
      setQuestion("");
    },
  });

  const navKey = viewerSource?.navKey;
  useEffect(() => {
    if (navKey && typeof window !== "undefined" && window.innerWidth < 1024) {
      viewerRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [navKey]);

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
  const sourceMap = new Map(sources.map((s) => [s.id, s.title]));
  const hasReadySource = sources.some((s) => s.status === "ready");

  const handleAskSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (askMutation.isPending || !hasReadySource) return;

    const trimmed = question.trim();
    if (!trimmed || trimmed.length > 2000) return;

    askMutation.mutate({
      question: trimmed,
      allow_outside: allowOutside,
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleAskSubmit();
    }
  };

  return (
    <main
      className={`p-8 space-y-8 ${
        viewerSource ? "max-w-7xl mx-auto" : "max-w-4xl mx-auto"
      }`}
    >
      <div className="space-y-4">
        <div>
          <Link href="/notebooks" className="text-sm text-muted-foreground hover:underline">
            ← Back to notebooks
          </Link>
        </div>
        <AccountBar />
      </div>

      <div className={viewerSource ? "flex flex-col lg:flex-row gap-8 items-start" : ""}>
        <div className={viewerSource ? "w-full lg:w-1/2 space-y-8 min-w-0" : "space-y-8"}>
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
                    <div className="flex items-center justify-between gap-4">
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="font-medium truncate">{src.title}</span>
                        {src.status === "ready" && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              openSource({ sourceId: src.id, title: src.title, page: 1 })
                            }
                          >
                            Open
                          </Button>
                        )}
                      </div>
                      <span className="text-sm text-muted-foreground capitalize shrink-0">{src.status}</span>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                      {src.page_count != null && <span>{src.page_count} pages</span>}
                      {src.error && <span className="text-muted-foreground">{src.error}</span>}
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

          {/* Ask section */}
          <section className="space-y-6 pt-4 border-t">
            <div className="space-y-1">
              <h2 className="text-lg font-semibold">Ask your sources</h2>
              <p className="text-xs text-muted-foreground">
                Ask questions grounded directly in your uploaded materials.
              </p>
            </div>

            {sourcesQuery.isPending ? (
              <div className="rounded-lg border p-4 bg-muted/20">
                <p className="text-sm text-muted-foreground">
                  Loading sources…
                </p>
              </div>
            ) : !hasReadySource ? (
              <div className="rounded-lg border p-4 bg-muted/20">
                <p className="text-sm text-muted-foreground">
                  Upload a PDF and wait until it&apos;s ready before asking.
                </p>
              </div>
            ) : (
              <form onSubmit={handleAskSubmit} className="space-y-4">
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ask-question">Question</Label>
                    <span className="text-xs text-muted-foreground">
                      {question.length}/2000
                    </span>
                  </div>
                  <Textarea
                    id="ask-question"
                    value={question}
                    onChange={(e) => setQuestion(e.target.value.slice(0, 2000))}
                    onKeyDown={handleKeyDown}
                    placeholder="What would you like to know from your course materials?"
                    rows={3}
                    disabled={askMutation.isPending}
                    required
                  />
                </div>

                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="allow-outside"
                    checked={allowOutside}
                    onChange={(e) => setAllowOutside(e.target.checked)}
                    disabled={askMutation.isPending}
                    className="h-4 w-4 rounded border-border"
                  />
                  <Label htmlFor="allow-outside" className="text-xs font-normal cursor-pointer">
                    Allow answers beyond my course
                  </Label>
                </div>

                <div className="flex items-center gap-3">
                  <Button
                    type="submit"
                    disabled={askMutation.isPending || !question.trim()}
                  >
                    {askMutation.isPending ? "Thinking…" : "Ask"}
                  </Button>
                </div>

                {askMutation.isPending && (
                  <p className="text-sm text-muted-foreground">
                    Thinking… this usually takes a few seconds, sometimes up to a minute or two.
                  </p>
                )}

                {askMutation.isError && (
                  <div className="p-3 border rounded-lg">
                    <p className="text-sm text-muted-foreground">
                      {formatAskError(askMutation.error)}
                    </p>
                  </div>
                )}
              </form>
            )}

            {/* Answer history for this visit */}
            {answers.length > 0 && (
              <div className="space-y-6 pt-4 border-t">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold">Answers</h3>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setAnswers([])}
                    className="text-xs"
                  >
                    Clear
                  </Button>
                </div>

                <div className="space-y-6 divide-y">
                  {answers.map((item, idx) => (
                    <div
                      key={item.id}
                      className={idx > 0 ? "pt-6 space-y-3" : "space-y-3"}
                    >
                      <div className="space-y-1">
                        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
                          Question
                        </p>
                        <p className="text-sm font-medium">{item.question}</p>
                      </div>

                      <div className="space-y-1">
                        <p className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
                          Answer
                        </p>
                        <AnswerView
                          response={item.response}
                          sourceMap={sourceMap}
                          onOpenPdf={(sourceId, title, page) =>
                            openSource({ sourceId, title, page })
                          }
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>

        {viewerSource && (
          <aside
            ref={viewerRef}
            className="w-full lg:w-1/2 lg:sticky lg:top-6 lg:h-[calc(100vh-3rem)] lg:overflow-y-auto rounded-lg border bg-card p-4"
          >
            <PdfViewer
              key={viewerSource.sourceId}
              notebookId={id}
              sourceId={viewerSource.sourceId}
              title={viewerSource.title}
              page={viewerSource.page}
              onPageChange={setPage}
              onClose={closeViewer}
            />
          </aside>
        )}
      </div>
    </main>
  );
}
