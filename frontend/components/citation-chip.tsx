"use client";

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
        {citation.label}
      </Button>
    );
  }

  if (open.kind === "youtube") {
    return (
      <a
        href={open.url}
        target="_blank"
        rel="noopener noreferrer"
        className={buttonVariants({ variant: "outline", size: "sm" }) + " text-xs"}
      >
        {citation.label}
      </a>
    );
  }

  return null;
}
