"use client";

import { useMemo, useRef, useState, useEffect, useLayoutEffect } from "react";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import {
  ApiError,
  createChat,
  listChats,
  listChatMessages,
  sendChatMessage,
  type ChatMessageListResponse,
  type ContextChunk,
  type MessageOut,
} from "@/lib/api";
import { formatAskError } from "@/lib/answer-errors";
import { useTopics } from "@/lib/use-topics";
import { ChatMessage } from "@/components/chat-message";
import { ChatInput } from "@/components/chat-input";
import { Button } from "@/components/ui/button";

export interface ChatPanelProps {
  notebookId: string;
  uid: string;
  active: boolean;
  chatSelection: {
    scope: string | null;
    chatId: string | "new" | null;
  };
  onSelectionChange: (sel: {
    scope: string | null;
    chatId: string | "new" | null;
  }) => void;
  onBusyChange: (busy: boolean) => void;
  hasReadySource: boolean;
  sourceMap: Map<string, string>;
}

export function ChatPanel({
  notebookId,
  uid,
  active,
  chatSelection,
  onSelectionChange,
  onBusyChange,
  hasReadySource,
  sourceMap,
}: ChatPanelProps) {
  const queryClient = useQueryClient();

  const topicsQuery = useTopics(notebookId, uid);

  // chats_list is called once per notebook with limit 50 (backend max)
  const chatsQuery = useQuery({
    queryKey: ["chats", uid, notebookId],
    queryFn: () => listChats(notebookId, undefined, 50),
    enabled: !!uid && !!notebookId,
    retry: false,
  });

  // Deriving effectiveChatId
  let effectiveChatId: string | null = null;
  if (chatSelection.chatId === "new") {
    effectiveChatId = null;
  } else if (typeof chatSelection.chatId === "string") {
    effectiveChatId = chatSelection.chatId;
  } else {
    const matchingChat = chatsQuery.data?.items?.find(
      (c) => c.topic_id === (chatSelection.scope ?? null)
    );
    effectiveChatId = matchingChat ? matchingChat.id : null;
  }

  // Messages infinite query with staleTime: Infinity
  const messagesQuery = useInfiniteQuery({
    queryKey: ["chat-messages", uid, notebookId, effectiveChatId],
    queryFn: ({ pageParam }) => listChatMessages(notebookId, effectiveChatId!, pageParam, 30),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !!uid && !!notebookId && !!effectiveChatId,
    retry: false,
    staleTime: Infinity,
  });

  // Oldest first messages sequence
  const messages = useMemo(() => {
    if (!messagesQuery.data) return [];
    return messagesQuery.data.pages.slice().reverse().flatMap((p) => p.items);
  }, [messagesQuery.data]);

  const [draftText, setDraftText] = useState("");
  const [allowOutside, setAllowOutside] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isLongWait, setIsLongWait] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [optimisticMessage, setOptimisticMessage] = useState<MessageOut | null>(null);
  const [visitContextMap, setVisitContextMap] = useState<Map<string, ContextChunk[]>>(new Map());

  // jumpForChatId tracks chat ID of the reply; showJumpToLatest derived during render
  const [jumpForChatId, setJumpForChatId] = useState<string | null>(null);
  const showJumpToLatest = Boolean(
    jumpForChatId && effectiveChatId && jumpForChatId === effectiveChatId
  );

  const inFlightRef = useRef(false);
  const pendingScroll = useRef<"bottom" | { prevScrollHeight: number; prevScrollTop: number } | null>(null);
  const lastScrolledChatIdRef = useRef<string | null>(null);
  const savedScrollTopRef = useRef<number>(0);
  const wasActiveRef = useRef(active);
  const longWaitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const bottomAnchorRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  // Reset savedScrollTopRef when chat ID changes
  useLayoutEffect(() => {
    savedScrollTopRef.current = 0;
  }, [effectiveChatId]);

  // Unmount cleanup
  useEffect(() => {
    return () => {
      if (longWaitTimerRef.current) {
        clearTimeout(longWaitTimerRef.current);
      }
    };
  }, []);

  // Tracking onScroll
  const handleScroll = () => {
    const container = scrollContainerRef.current;
    if (!container || container.clientHeight === 0) return;
    savedScrollTopRef.current = container.scrollTop;

    if (container.scrollHeight - container.scrollTop - container.clientHeight <= 120) {
      setJumpForChatId(null);
    }
  };

  // Layout effect for scroll positioning
  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    // reset lastScrolledChatIdRef only when no effectiveChatId or empty state is confirmed
    if (!effectiveChatId || (messages.length === 0 && !optimisticMessage && messagesQuery.isSuccess)) {
      lastScrolledChatIdRef.current = null;
    }

    if (!active) {
      wasActiveRef.current = false;
      return;
    }

    const justBecameActive = !wasActiveRef.current;
    wasActiveRef.current = true;

    // Deferral if clientHeight === 0
    if (container.clientHeight === 0) {
      const frameId = requestAnimationFrame(() => {
        const cont = scrollContainerRef.current;
        if (!cont || cont.clientHeight === 0) return;
        if (pendingScroll.current === "bottom") {
          cont.scrollTop = cont.scrollHeight;
          pendingScroll.current = null;
        } else if (pendingScroll.current && typeof pendingScroll.current === "object") {
          const { prevScrollHeight, prevScrollTop } = pendingScroll.current;
          cont.scrollTop = prevScrollTop + (cont.scrollHeight - prevScrollHeight);
          pendingScroll.current = null;
        } else if (justBecameActive && savedScrollTopRef.current > 0) {
          cont.scrollTop = savedScrollTopRef.current;
        }
      });
      return () => cancelAnimationFrame(frameId);
    }

    // Pending scroll takes priority
    if (pendingScroll.current === "bottom") {
      container.scrollTop = container.scrollHeight;
      pendingScroll.current = null;
      return;
    }
    if (pendingScroll.current && typeof pendingScroll.current === "object") {
      const { prevScrollHeight, prevScrollTop } = pendingScroll.current;
      container.scrollTop = prevScrollTop + (container.scrollHeight - prevScrollHeight);
      pendingScroll.current = null;
      return;
    }

    // Open-scroll branch: runs only when nothing is pending
    if (effectiveChatId && lastScrolledChatIdRef.current !== effectiveChatId && messages.length > 0) {
      container.scrollTop = container.scrollHeight;
      lastScrolledChatIdRef.current = effectiveChatId;
      return;
    }

    // Restoring saved scrollTop ONLY on transition from inactive to active
    if (justBecameActive && savedScrollTopRef.current > 0) {
      container.scrollTop = savedScrollTopRef.current;
    }
  }, [active, effectiveChatId, messages.length, optimisticMessage, messagesQuery.isSuccess]);

  // Load older messages handler
  const handleLoadOlder = async () => {
    const container = scrollContainerRef.current;
    if (!container || messagesQuery.isFetchingNextPage) return;

    pendingScroll.current = {
      prevScrollHeight: container.scrollHeight,
      prevScrollTop: container.scrollTop,
    };

    const res = await messagesQuery.fetchNextPage();
    if (res.isError) {
      pendingScroll.current = null;
    }
  };

  // Jump to latest handler
  const handleJumpToLatest = () => {
    pendingScroll.current = "bottom";
    setJumpForChatId(null);
    const container = scrollContainerRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  };

  // Send message handler
  const handleSend = async () => {
    // Synchronous pre-flight guards
    if (inFlightRef.current || !hasReadySource || isSending || !chatsQuery.isSuccess) return;

    const trimmed = draftText.trim();
    if (!trimmed || trimmed.length > 2000) return;

    // Set guard ref FIRST, before any other step
    inFlightRef.current = true;

    const capturedText = trimmed;
    const capturedAllowOutside = allowOutside;
    const capturedScope = chatSelection.scope;
    const capturedUid = uid;
    const capturedNotebookId = notebookId;
    let targetChatId = effectiveChatId;

    let createFailed = false;
    let createdNewChatId: string | null = null;

    try {
      setIsSending(true);
      onBusyChange(true);
      setSendError(null);

      pendingScroll.current = "bottom";

      setDraftText("");
      setOptimisticMessage({
        id: `optimistic-${Date.now()}`,
        role: "user",
        text: capturedText,
        created_at: new Date().toISOString(),
        refs: { sources: [] },
      });

      setIsLongWait(false);
      longWaitTimerRef.current = setTimeout(() => {
        setIsLongWait(true);
      }, 15000);

      if (!targetChatId) {
        try {
          const createdChat = await createChat(capturedNotebookId, {
            topic_id: capturedScope ?? undefined,
          });
          targetChatId = createdChat.id;
          createdNewChatId = createdChat.id;

          // Invalidate chats list immediately
          queryClient.invalidateQueries({
            queryKey: ["chats", capturedUid, capturedNotebookId],
          });

          // Correction 1: set lastScrolledChatIdRef BEFORE updating chatSelection
          lastScrolledChatIdRef.current = createdChat.id;

          // Retain created chat ID in page state
          onSelectionChange({ scope: capturedScope, chatId: createdChat.id });
        } catch (createErr) {
          createFailed = true;
          throw createErr;
        }
      }

      const res = await sendChatMessage(capturedNotebookId, targetChatId, {
        text: capturedText,
        allow_outside: capturedAllowOutside,
      });

      // measure near bottom BEFORE updating cache
      const container = scrollContainerRef.current;
      const isNearBottom = container
        ? container.scrollHeight - container.scrollTop - container.clientHeight <= 120
        : true;

      if (isNearBottom) {
        pendingScroll.current = "bottom";
        setJumpForChatId(null);
      } else {
        pendingScroll.current = null;
        setJumpForChatId(targetChatId);
      }

      await queryClient.cancelQueries({
        queryKey: ["chat-messages", capturedUid, capturedNotebookId, targetChatId],
      });

      if (res.assistant_message.context && res.assistant_message.context.length > 0) {
        setVisitContextMap((prev) => {
          const next = new Map(prev);
          next.set(res.assistant_message.id, res.assistant_message.context!);
          return next;
        });
      }

      if (createdNewChatId) {
        queryClient.setQueryData(
          ["chat-messages", capturedUid, capturedNotebookId, targetChatId],
          {
            pages: [
              {
                items: [res.user_message, res.assistant_message],
                next_cursor: null,
              },
            ],
            pageParams: [undefined],
          }
        );
      } else {
        const cachedData = queryClient.getQueryData<InfiniteData<ChatMessageListResponse>>(
          ["chat-messages", capturedUid, capturedNotebookId, targetChatId]
        );
        if (!cachedData || cachedData.pages.length === 0) {
          queryClient.invalidateQueries({
            queryKey: ["chat-messages", capturedUid, capturedNotebookId, targetChatId],
          });
        } else {
          queryClient.setQueryData(
            ["chat-messages", capturedUid, capturedNotebookId, targetChatId],
            (oldData: InfiniteData<ChatMessageListResponse> | undefined) => {
              if (!oldData || oldData.pages.length === 0) return oldData;
              const newPages = [...oldData.pages];
              newPages[0] = {
                ...newPages[0],
                items: [...newPages[0].items, res.user_message, res.assistant_message],
              };
              return { ...oldData, pages: newPages };
            }
          );
        }
      }

      queryClient.invalidateQueries({
        queryKey: ["chats", capturedUid, capturedNotebookId],
      });

      setOptimisticMessage(null);
    } catch (err) {
      setOptimisticMessage(null);
      setDraftText(capturedText);
      if (createFailed && err instanceof ApiError && err.code === "timeout") {
        setSendError("Creating the chat took too long. Try again.");
      } else {
        setSendError(formatAskError(err));
      }
    } finally {
      // All cleanup guaranteed in finally
      if (longWaitTimerRef.current) {
        clearTimeout(longWaitTimerRef.current);
        longWaitTimerRef.current = null;
      }
      setIsLongWait(false);
      setIsSending(false);
      inFlightRef.current = false;
      onBusyChange(false);
      // Return focus to chat input after React has re-enabled it
      requestAnimationFrame(() =>
        requestAnimationFrame(() => inputRef.current?.focus())
      );
    }
  };

  // Scope topics computation
  const availableTopics = useMemo(() => {
    const items = topicsQuery.data?.items ?? [];
    const courseTopics = items.filter((t) => !t.is_other);
    courseTopics.sort((a, b) => a.order - b.order);
    const otherTopics = items.filter((t) => t.is_other && t.location_count > 0);
    return [...courseTopics, ...otherTopics];
  }, [topicsQuery.data?.items]);

  const currentScopeName = useMemo(() => {
    if (!chatSelection.scope) return "notebook";
    const t = topicsQuery.data?.items?.find((item) => item.id === chatSelection.scope);
    return t ? t.name : "topic";
  }, [chatSelection.scope, topicsQuery.data?.items]);

  // empty state condition
  const isEmpty =
    (!effectiveChatId || messagesQuery.isSuccess) &&
    messages.length === 0 &&
    !optimisticMessage;

  return (
    <div className="flex flex-col h-full min-h-0 bg-background">
      {/* Chat Header */}
      <div className="flex items-center justify-between border-b px-4 py-2 shrink-0 bg-background">
        <div className="flex items-center gap-2">
          {availableTopics.length > 0 && (
            <select
              id="chat-scope-select"
              value={chatSelection.scope ?? ""}
              onChange={(e) => {
                const val = e.target.value === "" ? null : e.target.value;
                setSendError(null);
                setJumpForChatId(null);
                onSelectionChange({ scope: val, chatId: null });
              }}
              disabled={isSending}
              className="h-8 rounded-md border border-input bg-background px-2.5 py-1 text-xs text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              aria-label="Chat scope"
            >
              <option value="">Whole notebook</option>
              {availableTopics.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.is_other ? t.name : `${t.order} · ${t.name}`}
                </option>
              ))}
            </select>
          )}
          {availableTopics.length === 0 && (
            <span className="text-xs font-medium text-muted-foreground">Whole notebook</span>
          )}
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-8 px-2.5 text-xs"
          onClick={() => {
            setSendError(null);
            setJumpForChatId(null);
            onSelectionChange({ scope: chatSelection.scope, chatId: "new" });
          }}
          disabled={isSending || effectiveChatId === null}
        >
          New chat
        </Button>
      </div>

      {/* Messages Scroll Container */}
      <div
        ref={scrollContainerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-4 space-y-4 [overflow-anchor:none]"
      >
        {/* chats_list loading or error in conversation area */}
        {chatsQuery.isPending ? (
          <div className="h-full flex flex-col items-center justify-center p-8 text-center text-muted-foreground">
            <p className="text-sm">Loading chats…</p>
          </div>
        ) : chatsQuery.isError ? (
          <div className="h-full flex flex-col items-center justify-center p-8 text-center space-y-3">
            <p className="text-sm text-muted-foreground">{"Couldn't load chats."}</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => chatsQuery.refetch()}
              className="text-xs"
            >
              Try again
            </Button>
          </div>
        ) : messagesQuery.isError ? (
          <div className="flex flex-col items-center justify-center p-8 text-center space-y-3">
            <p className="text-sm text-muted-foreground">
              {messagesQuery.error instanceof ApiError && messagesQuery.error.status === 404
                ? "Chat not found."
                : "Failed to load messages."}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => messagesQuery.refetch()}
              className="text-xs"
            >
              Try again
            </Button>
          </div>
        ) : effectiveChatId && messagesQuery.isPending && messages.length === 0 && !optimisticMessage ? (
          <div className="h-full flex flex-col items-center justify-center p-8 text-center text-muted-foreground">
            <p className="text-sm">Loading messages…</p>
          </div>
        ) : isEmpty ? (
          /* empty state */
          <div className="h-full flex flex-col items-center justify-center p-8 text-center text-muted-foreground">
            <p className="text-sm font-medium">Ask anything about this {currentScopeName}</p>
            <p className="text-xs mt-1">Questions will be answered using your course materials.</p>
          </div>
        ) : (
          <>
            {messagesQuery.hasNextPage && (
              <div className="flex justify-center pt-1 pb-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleLoadOlder}
                  disabled={messagesQuery.isFetchingNextPage}
                  className="text-xs h-7"
                >
                  {messagesQuery.isFetchingNextPage ? "Loading older messages…" : "Load older messages"}
                </Button>
              </div>
            )}

            {messages.map((m) => (
              <ChatMessage
                key={m.id}
                message={m}
                sourceMap={sourceMap}
                contextChunks={visitContextMap.get(m.id)}
              />
            ))}

            {optimisticMessage && (
              <div className="space-y-4">
                <ChatMessage
                  message={optimisticMessage}
                  sourceMap={sourceMap}
                />
                <div className="flex items-center gap-2 p-3 rounded-lg border bg-muted/20 text-xs text-muted-foreground">
                  <div className="flex items-center gap-1 motion-safe:animate-pulse">
                    <span className="h-1.5 w-1.5 rounded-full bg-foreground/60" />
                    <span className="h-1.5 w-1.5 rounded-full bg-foreground/60" />
                    <span className="h-1.5 w-1.5 rounded-full bg-foreground/60" />
                  </div>
                  <span>
                    {isLongWait
                      ? "Still working… The AI model is busy, so this can take up to three minutes."
                      : "Thinking… this usually takes a few seconds, sometimes up to a minute or two."}
                  </span>
                </div>
              </div>
            )}
          </>
        )}

        <div ref={bottomAnchorRef} className="h-0 w-0" />
      </div>

      {/* Floating Jump to latest button */}
      {showJumpToLatest && (
        <div className="relative">
          <div className="absolute bottom-3 left-0 right-0 flex justify-center z-10 pointer-events-none">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="shadow-md bg-background pointer-events-auto text-xs"
              onClick={handleJumpToLatest}
            >
              Jump to latest ↓
            </Button>
          </div>
        </div>
      )}

      {/* Chat Input (disabled when chatsQuery is not success) */}
      <ChatInput
        inputRef={inputRef}
        draft={draftText}
        onDraftChange={setDraftText}
        allowOutside={allowOutside}
        onAllowOutsideChange={setAllowOutside}
        onSend={handleSend}
        disabled={!chatsQuery.isSuccess}
        isSending={isSending}
        hasReadySource={hasReadySource}
        error={sendError}
      />
    </div>
  );
}
