"use client";

import type { RefObject } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export interface ChatInputProps {
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  draft: string;
  onDraftChange: (value: string) => void;
  allowOutside: boolean;
  onAllowOutsideChange: (value: boolean) => void;
  onSend: () => void;
  disabled: boolean;
  isSending: boolean;
  hasReadySource: boolean;
  error: string | null;
}

export function ChatInput({
  inputRef,
  draft,
  onDraftChange,
  allowOutside,
  onAllowOutsideChange,
  onSend,
  disabled,
  isSending,
  hasReadySource,
  error,
}: ChatInputProps) {
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onSend();
    }
  };

  const isInputDisabled = disabled || isSending || !hasReadySource;
  const canSubmit = !isInputDisabled && draft.trim().length > 0 && draft.trim().length <= 2000;

  return (
    <div className="border-t p-4 space-y-3 bg-background shrink-0">
      {!hasReadySource && (
        <p className="text-xs text-muted-foreground">
          Add a source to start chatting
        </p>
      )}

      {error && (
        <div className="p-3 border rounded-lg bg-muted/10">
          <p className="text-sm text-muted-foreground">{error}</p>
        </div>
      )}

      <div className="space-y-2">
        <div className="relative">
          <Textarea
            ref={inputRef}
            id="chat-question"
            value={draft}
            onChange={(e) => onDraftChange(e.target.value.slice(0, 2000))}
            onKeyDown={handleKeyDown}
            placeholder={
              hasReadySource
                ? "Ask a question about this topic… (Enter to send, Shift+Enter for new line)"
                : "Add a source to start chatting"
            }
            rows={3}
            disabled={isInputDisabled}
            className="resize-none pr-16"
          />
          <span className="absolute bottom-2 right-2 text-[10px] text-muted-foreground pointer-events-none">
            {draft.length}/2000
          </span>
        </div>

        <div className="flex items-center justify-between gap-2 pt-1">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="allow-outside"
              checked={allowOutside}
              onChange={(e) => onAllowOutsideChange(e.target.checked)}
              disabled={isSending || disabled}
              className="h-4 w-4 rounded border-border"
            />
            <Label
              htmlFor="allow-outside"
              className="text-xs font-normal cursor-pointer text-muted-foreground"
            >
              Allow answers beyond my course
            </Label>
          </div>

          <Button
            type="button"
            onClick={onSend}
            disabled={!canSubmit}
            size="sm"
            className="h-8 px-3 text-xs"
          >
            {isSending ? "Thinking…" : "Send"}
          </Button>
        </div>
      </div>
    </div>
  );
}
