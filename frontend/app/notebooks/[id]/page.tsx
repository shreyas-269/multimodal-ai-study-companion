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
import {
  ApiError,
  getNotebook,
  listSources,
  uploadSource,
} from "@/lib/api";
import { ViewerProvider, useViewer } from "@/components/viewer-context";
import { SourceCard } from "@/components/source-card";
import { TopicList } from "@/components/topic-list";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { QuizPanel } from "@/components/quiz/quiz-panel";
import { ChatPanel } from "@/components/chat-panel";

const PdfViewer = dynamic(
  () => import("@/components/pdf-viewer").then((mod) => mod.PdfViewer),
  {
    ssr: false,
    loading: () => <p className="text-sm text-muted-foreground p-4">Loading PDF viewer…</p>,
  }
);

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
  const centreRef = useRef<HTMLElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const [activeTab, setActiveTab] = useState<"chat" | "quiz">("chat");
  const [quizRequest, setQuizRequest] = useState<{ topicIds: string[]; nonce: number } | null>(null);
  const [hasOpenedQuiz, setHasOpenedQuiz] = useState(false);

  const [chatSelection, setChatSelection] = useState<{
    scope: string | null;
    chatId: string | "new" | null;
  }>({ scope: null, chatId: null });
  const [chatBusy, setChatBusy] = useState(false);

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
        queryClient.invalidateQueries({ queryKey: ["topics", user.uid, id] });
        queryClient.invalidateQueries({ queryKey: ["topic-sources", user.uid, id] });
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

  const handleChatFromTopic = (topicId: string) => {
    if (chatBusy) return;
    setChatSelection({ scope: topicId, chatId: null });
    setActiveTab("chat");
    if (typeof window !== "undefined" && window.innerWidth < 1024) {
      centreRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  };

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
  const sourceMap = new Map<string, string>();
  if (notebook.sources_summary) {
    for (const s of notebook.sources_summary) {
      if (s.source_id && s.title) {
        sourceMap.set(s.source_id, s.title);
      }
    }
  }
  for (const s of sources) {
    if (s.id && s.title) {
      sourceMap.set(s.id, s.title);
    }
  }
  const hasReadySource = sources.some((s) => s.status === "ready");

  return (
    <div className="flex min-h-screen flex-col lg:h-screen">
      <div className="flex flex-wrap items-center gap-3 border-b px-4 py-2">
        <Link href="/notebooks" className="text-sm text-muted-foreground hover:underline">
          ← Notebooks
        </Link>
        <h1 className="text-base font-semibold truncate min-w-0">{notebook.name}</h1>
        {notebook.is_demo && (
          <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold text-secondary-foreground">
            Demo
          </span>
        )}
        <span className="text-xs text-muted-foreground capitalize">
          Status: {notebook.status}
        </span>
        <div className="ml-auto">
          <AccountBar compact />
        </div>
      </div>

      <main className="flex flex-1 flex-col lg:min-h-0 lg:flex-row">
        <aside
          aria-label="Sources"
          className="space-y-6 p-4 lg:w-72 lg:shrink-0 lg:overflow-y-auto lg:border-r"
        >
          {/* F2: the flat topic list goes here, above Sources */}
          <TopicList
            notebookId={id}
            uid={user.uid}
            onQuiz={(topicId) => {
              setQuizRequest((prev) => ({ topicIds: [topicId], nonce: (prev?.nonce ?? 0) + 1 }));
              setActiveTab("quiz");
              setHasOpenedQuiz(true);
              if (typeof window !== "undefined" && window.innerWidth < 1024) {
                centreRef.current?.scrollIntoView({ behavior: "smooth" });
              }
            }}
            onChat={handleChatFromTopic}
            chatDisabled={chatBusy}
          />

          <section className="space-y-3">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">Sources</h2>
              {sources.length > 0 && (
                <span className="text-xs text-muted-foreground">{sources.length}</span>
              )}
            </div>

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
              <div className="grid gap-3">
                {sources.map((src) => (
                  <SourceCard
                    key={src.id}
                    source={src}
                    onOpenPdf={(sourceId, title) =>
                      openSource({ sourceId, title, page: 1 })
                    }
                  />
                ))}
              </div>
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

          <section className="space-y-3">
            <h2 className="text-sm font-semibold">Add a source</h2>
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
        </aside>

        <section
          ref={centreRef}
          aria-label="Chat and quiz"
          className="min-w-0 flex-1 flex flex-col lg:min-h-0 lg:overflow-hidden"
        >
          <Tabs
            value={activeTab}
            onValueChange={(val) => {
              if (val === "chat" || val === "quiz") {
                setActiveTab(val);
                if (val === "quiz") setHasOpenedQuiz(true);
              }
            }}
            className="flex flex-col flex-1 min-h-0"
          >
            <div className="border-b px-6 py-2 shrink-0">
              <TabsList>
                <TabsTrigger value="chat">Chat</TabsTrigger>
                <TabsTrigger value="quiz">Quiz</TabsTrigger>
              </TabsList>
            </div>

            <TabsContent
              value="chat"
              keepMounted
              className="min-w-0 flex flex-col flex-none h-[70vh] lg:flex-1 lg:h-auto lg:min-h-0 mt-0 data-[hidden]:hidden [&[hidden]]:hidden"
            >
              <ChatPanel
                notebookId={id}
                uid={user.uid}
                active={activeTab === "chat"}
                chatSelection={chatSelection}
                onSelectionChange={setChatSelection}
                onBusyChange={setChatBusy}
                hasReadySource={hasReadySource}
                sourceMap={sourceMap}
              />
            </TabsContent>

            <TabsContent
              value="quiz"
              keepMounted
              className="min-w-0 flex-1 lg:overflow-y-auto mt-0 data-[hidden]:hidden [&[hidden]]:hidden"
            >
              <div className="mx-auto w-full max-w-3xl space-y-6 p-6">
                <QuizPanel
                  notebookId={id}
                  uid={user.uid}
                  quizRequest={quizRequest}
                  hasOpenedQuiz={hasOpenedQuiz}
                />
              </div>
            </TabsContent>
          </Tabs>
        </section>

        {viewerSource && (
          <aside
            ref={viewerRef}
            aria-label="PDF viewer"
            className="w-full border-t bg-card p-4 lg:w-[42%] lg:shrink-0 lg:overflow-y-auto lg:border-l lg:border-t-0"
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
      </main>
    </div>
  );
}
