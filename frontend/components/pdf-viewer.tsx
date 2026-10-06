"use client";

import { useState, useRef, useEffect } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/components/auth-provider";
import { useViewer } from "@/components/viewer-context";
import { bboxToCssRect } from "@/lib/pdf-highlight";
import { getSourceFileRequest } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

const PDF_OPTIONS = {};

export interface PdfViewerProps {
  notebookId: string;
  sourceId: string;
  title: string;
  page: number;
  onPageChange: (page: number) => void;
  onClose: () => void;
}

export function PdfViewer({
  notebookId,
  sourceId,
  title,
  page,
  onPageChange,
  onClose,
}: PdfViewerProps) {
  const { user } = useAuth();
  const { highlight, viewerSource } = useViewer();
  const navKey = viewerSource?.navKey ?? 0;

  const containerRef = useRef<HTMLDivElement>(null);
  const highlightRef = useRef<HTMLDivElement>(null);
  const lastScrolledNavKeyRef = useRef<number | null>(null);

  const [containerWidth, setContainerWidth] = useState<number>(0);
  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageSize, setPageSize] = useState<{
    pageNumber: number;
    width: number;
    height: number;
  } | null>(null);
  const [renderedPageNumber, setRenderedPageNumber] = useState<number | null>(null);
  const [hasRenderError, setHasRenderError] = useState(false);
  const [submitCount, setSubmitCount] = useState<number>(0);

  const {
    data: fileRequest,
    isFetching: isTokenFetching,
    isError: isTokenError,
    refetch,
  } = useQuery({
    queryKey: ["source-file", user?.uid, notebookId, sourceId],
    queryFn: () => getSourceFileRequest(notebookId, sourceId),
    staleTime: 0,
    gcTime: 0,
  });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0) {
          setContainerWidth(entry.contentRect.width);
        }
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const renderedWidth = Math.floor(containerWidth);
  const isCurrentPageMeasured = pageSize != null && pageSize.pageNumber === page;
  const isCurrentPageHighlighted = highlight != null && highlight.page === page;

  const rect =
    isCurrentPageHighlighted && isCurrentPageMeasured && renderedWidth > 0
      ? bboxToCssRect(highlight.bbox, pageSize.width, pageSize.height, renderedWidth, 3)
      : null;

  useEffect(() => {
    if (
      renderedPageNumber === page &&
      highlight &&
      highlight.page === page &&
      rect &&
      highlightRef.current &&
      typeof window !== "undefined" &&
      window.matchMedia("(min-width: 1024px)").matches &&
      lastScrolledNavKeyRef.current !== navKey
    ) {
      lastScrolledNavKeyRef.current = navKey;
      highlightRef.current.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }, [renderedPageNumber, page, highlight, rect, navKey]);

  const handleError = () => {
    setHasRenderError(true);
  };

  const handleTryAgain = () => {
    setHasRenderError(false);
    refetch();
  };

  const handleLoadSuccess = ({ numPages: total }: { numPages: number }) => {
    setNumPages(total);
    if (page > total) {
      onPageChange(total);
    }
  };

  const handlePageSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const raw = String(formData.get("page") ?? "").trim();
    if (!raw) return;
    const num = Math.floor(Number(raw));
    if (!Number.isNaN(num)) {
      setSubmitCount((c) => c + 1);
      const max = numPages ?? 1;
      const clamped = Math.max(1, Math.min(max, num));
      onPageChange(clamped);
    }
  };

  const isErrorState = isTokenError || hasRenderError;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 pb-2 border-b">
        <h3 className="font-semibold text-sm truncate" title={title}>
          {title}
        </h3>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>

      {/* Toolbar */}
      {!isErrorState && !isTokenFetching && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page - 1)}
              disabled={page <= 1}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => onPageChange(page + 1)}
              disabled={numPages != null && page >= numPages}
            >
              Next
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <span>
              Page {page} of {numPages ?? "…"}
            </span>
            <form onSubmit={handlePageSubmit} className="flex items-center">
              <Input
                key={`${page}-${submitCount}`}
                name="page"
                defaultValue={page}
                className="w-14 h-8 text-center text-sm"
                aria-label="Page number"
              />
            </form>
          </div>
        </div>
      )}

      {/* Content */}
      <div ref={containerRef} className="w-full min-h-[300px]">
        {isTokenFetching && <p className="text-sm text-muted-foreground p-4">Loading PDF…</p>}

        {!isTokenFetching && isErrorState && (
          <div className="p-4 space-y-3">
            <p className="text-sm text-muted-foreground">Couldn&apos;t load this PDF.</p>
            <Button variant="outline" size="sm" onClick={handleTryAgain}>
              Try again
            </Button>
          </div>
        )}

        {!isTokenFetching && !isErrorState && fileRequest && containerWidth > 0 && (
          <Document
            file={fileRequest}
            options={PDF_OPTIONS}
            onLoadSuccess={handleLoadSuccess}
            onLoadError={handleError}
            onSourceError={handleError}
            loading={<p className="text-sm text-muted-foreground p-4">Loading PDF…</p>}
            suspense={false}
          >
            <Page
              pageNumber={page}
              width={renderedWidth}
              renderTextLayer={true}
              renderAnnotationLayer={true}
              onLoadSuccess={(pdfPage) => {
                const vp = pdfPage.getViewport({ scale: 1 });
                setPageSize({
                  pageNumber: pdfPage.pageNumber,
                  width: vp.width,
                  height: vp.height,
                });
              }}
              onRenderSuccess={() => {
                setRenderedPageNumber(page);
              }}
              onRenderError={handleError}
            >
              {rect && (
                <div
                  ref={highlightRef}
                  aria-hidden="true"
                  className="pointer-events-none absolute z-10 rounded-sm border-2 border-black bg-black/5 shadow-[0_0_0_2px_white]"
                  style={{
                    left: rect.left,
                    top: rect.top,
                    width: rect.width,
                    height: rect.height,
                  }}
                />
              )}
            </Page>
          </Document>
        )}
      </div>
    </div>
  );
}
