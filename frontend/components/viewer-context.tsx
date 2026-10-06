"use client";

import { createContext, useContext, useState, ReactNode } from "react";
import { toBbox, type Bbox } from "@/lib/pdf-highlight";

export interface ViewerSource {
  sourceId: string;
  title: string;
  page: number; // 1-based
  navKey: number;
}

export interface Highlight {
  page: number;
  bbox: Bbox;
}

export interface OpenSourceOptions {
  sourceId: string;
  title: string;
  page?: number;
  bbox?: unknown;
}

interface ViewerContextType {
  viewerSource: ViewerSource | null;
  highlight: Highlight | null;
  openSource: (options: OpenSourceOptions) => void;
  setPage: (page: number) => void;
  closeViewer: () => void;
}

const ViewerContext = createContext<ViewerContextType | null>(null);

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [viewerSource, setViewerSource] = useState<ViewerSource | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);

  const openSource = ({ sourceId, title, page = 1, bbox }: OpenSourceOptions) => {
    const targetPage = Math.max(1, page);
    const validBbox = toBbox(bbox);
    setHighlight(validBbox ? { page: targetPage, bbox: validBbox } : null);
    setViewerSource((prev) => ({
      sourceId,
      title,
      page: targetPage,
      navKey: (prev?.navKey ?? 0) + 1,
    }));
  };

  const setPage = (page: number) => {
    setViewerSource((prev) => (prev ? { ...prev, page: Math.max(1, page) } : null));
    setHighlight(null);
  };

  const closeViewer = () => {
    setViewerSource(null);
    setHighlight(null);
  };

  return (
    <ViewerContext.Provider
      value={{ viewerSource, highlight, openSource, setPage, closeViewer }}
    >
      {children}
    </ViewerContext.Provider>
  );
}

export function useViewer() {
  const context = useContext(ViewerContext);
  if (!context) {
    throw new Error("useViewer must be used within a ViewerProvider");
  }
  return context;
}
