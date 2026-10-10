"use client";

import type { MessageOut, ContextChunk } from "@/lib/api";
import { AnswerContent } from "@/components/answer";
import { MarkdownMath } from "@/components/markdown-math";

export interface ChatMessageProps {
  message: MessageOut;
  sourceMap: Map<string, string>;
  contextChunks?: ContextChunk[] | null;
}

export function ChatMessage({
  message,
  sourceMap,
  contextChunks,
}: ChatMessageProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-muted/60 px-4 py-2.5 text-sm text-foreground whitespace-pre-wrap break-words">
          {message.text}
        </div>
      </div>
    );
  }

  // Assistant message
  const hasParagraphs = message.paragraphs && message.paragraphs.length > 0;

  return (
    <div className="w-full space-y-2">
      {hasParagraphs ? (
        <AnswerContent
          paragraphs={message.paragraphs}
          context={contextChunks}
          sourceMap={sourceMap}
        />
      ) : message.text ? (
        <div className="text-sm leading-relaxed">
          <MarkdownMath content={message.text} />
        </div>
      ) : null}
    </div>
  );
}
