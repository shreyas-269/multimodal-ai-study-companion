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
import { Minus, Plus } from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url
).toString();

const PDF_OPTIONS = {};

const ZOOM_LEVELS = [1, 1.25, 1.5, 2, 3] as const;
type ZoomFactor = (typeof ZOOM_LEVELS)[number];

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

  const [zoom, setZoom] = useState<ZoomFactor>(1);
  const zoomRef = useRef<ZoomFactor>(zoom);
  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);
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

  const handleZoomOut = () => {
    const currentIndex = ZOOM_LEVELS.indexOf(zoom);
    if (currentIndex > 0) {
      setZoom(ZOOM_LEVELS[currentIndex - 1]);
    }
  };

  const handleZoomIn = () => {
    const currentIndex = ZOOM_LEVELS.indexOf(zoom);
    if (currentIndex < ZOOM_LEVELS.length - 1) {
      setZoom(ZOOM_LEVELS[currentIndex + 1]);
    }
  };

  const handleZoomFit = () => {
    setZoom(1);
  };

  const fitWidth = Math.floor(containerWidth);
  const renderedWidth = Math.floor(fitWidth * zoom);
  const isCurrentPageMeasured = pageSize != null && pageSize.pageNumber === page;
  const isCurrentPageHighlighted = highlight != null && highlight.page === page;

  // Outline is positioned in percentages of the rendered page box so it scales automatically with zoom and resizes.
  const rect =
    isCurrentPageHighlighted && isCurrentPageMeasured && renderedWidth > 0
      ? bboxToCssRect(highlight.bbox, pageSize.width, pageSize.height, renderedWidth, 3)
      : null;

  useEffect(() => {
    if (renderedPageNumber !== page || lastScrolledNavKeyRef.current === navKey) {
      return;
    }

    // Record navKey as handled at every zoom level so each citation navigation is processed exactly once.
    lastScrolledNavKeyRef.current = navKey;

    const hasOutline =
      highlight && highlight.page === page && rect && highlightRef.current;

    if (!hasOutline) {
      return;
    }

    const currentZoom = zoomRef.current;
    if (currentZoom === 1) {
      if (
        typeof window !== "undefined" &&
        window.matchMedia("(min-width: 1024px)").matches
      ) {
        highlightRef.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    } else {
      // Scrolling uses the viewer's own container and never scrollIntoView so the page around the viewer never jumps, especially on narrow screens.
      const container = containerRef.current;
      const highlightEl = highlightRef.current;
      if (container && highlightEl) {
        const containerBox = container.getBoundingClientRect();
        const highlightBox = highlightEl.getBoundingClientRect();

        const highlightCenterX = highlightBox.left + highlightBox.width / 2;
        const highlightCenterY = highlightBox.top + highlightBox.height / 2;

        const currentVisibleCenterX = containerBox.left + container.clientWidth / 2;
        const currentVisibleCenterY = containerBox.top + container.clientHeight / 2;

        const diffX = highlightCenterX - currentVisibleCenterX;
        const diffY = highlightCenterY - currentVisibleCenterY;

        const targetScrollLeft = container.scrollLeft + diffX;
        const targetScrollTop = container.scrollTop + diffY;

        const maxScrollLeft = Math.max(0, container.scrollWidth - container.clientWidth);
        const maxScrollTop = Math.max(0, container.scrollHeight - container.clientHeight);

        container.scrollLeft = Math.max(0, Math.min(maxScrollLeft, targetScrollLeft));
        container.scrollTop = Math.max(0, Math.min(maxScrollTop, targetScrollTop));
      }
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

          <div className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={handleZoomOut}
              disabled={zoom <= 1}
              aria-label="Zoom out"
            >
              <Minus />
            </Button>
            <span className="min-w-10 text-center text-xs font-medium tabular-nums">
              {zoom === 1 ? "Fit" : `${Math.round(zoom * 100)}%`}
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={handleZoomIn}
              disabled={zoom >= 3}
              aria-label="Zoom in"
            >
              <Plus />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleZoomFit}
              disabled={zoom === 1}
            >
              Fit width
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
      {/* Height is bounded and overflow enabled only when zoomed (> 1) so Fit mode stays exactly as before. */}
      <div
        ref={containerRef}
        className={
          zoom > 1
            ? "w-full min-h-[300px] overflow-auto max-h-[70vh] lg:max-h-[calc(100dvh-13.5rem)]"
            : "w-full min-h-[300px]"
        }
      >
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
                    left: `${rect.left}%`,
                    top: `${rect.top}%`,
                    width: `${rect.width}%`,
                    height: `${rect.height}%`,
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
