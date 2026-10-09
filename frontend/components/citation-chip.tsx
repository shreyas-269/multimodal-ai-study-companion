"use client";

import { FileText, Play, ExternalLink } from "lucide-react";
import type { components } from "@/lib/api-types";
import { useViewer } from "@/components/viewer-context";
import { Button, buttonVariants } from "@/components/ui/button";

export type Citation = components["schemas"]["Citation"];

export interface CitationChipProps {
  citation: Citation;
  title?: string;
}

export function CitationChip({ citation, title }: CitationChipProps) {
  const { openSource } = useViewer();
  const open = citation.open;

  if (open.kind === "pdf") {
    const resolvedTitle = title || citation.label;
    return (
      <Button
        variant="outline"
        size="sm"
        onClick={() =>
          openSource({
            sourceId: open.source_id,
            title: resolvedTitle,
            page: open.page,
            bbox: open.bbox ?? null,
          })
        }
        className="text-xs"
      >
        <FileText className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span>{citation.label}</span>
      </Button>
    );
  }

  if (open.kind === "youtube") {
    return (
      <a
        href={open.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-label={`${citation.label} (opens YouTube in a new tab)`}
        className={buttonVariants({ variant: "outline", size: "sm" }) + " text-xs"}
      >
        <Play className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span>{citation.label}</span>
        <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
      </a>
    );
  }

  return null;
}
