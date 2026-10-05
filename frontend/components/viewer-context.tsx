"use client";

import { createContext, useContext, useState, ReactNode } from "react";

export interface ViewerSource {
  sourceId: string;
  title: string;
  page: number; // 1-based
  navKey: number;
}

export interface OpenSourceOptions {
  sourceId: string;
  title: string;
  page?: number;
}

interface ViewerContextType {
  viewerSource: ViewerSource | null;
  openSource: (options: OpenSourceOptions) => void;
  setPage: (page: number) => void;
  closeViewer: () => void;
}

const ViewerContext = createContext<ViewerContextType | null>(null);

export function ViewerProvider({ children }: { children: ReactNode }) {
  const [viewerSource, setViewerSource] = useState<ViewerSource | null>(null);

  const openSource = ({ sourceId, title, page = 1 }: OpenSourceOptions) => {
    setViewerSource((prev) => ({
      sourceId,
      title,
      page: Math.max(1, page),
      navKey: (prev?.navKey ?? 0) + 1,
    }));
  };

  const setPage = (page: number) => {
    setViewerSource((prev) => (prev ? { ...prev, page: Math.max(1, page) } : null));
  };

  const closeViewer = () => {
    setViewerSource(null);
  };

  return (
    <ViewerContext.Provider value={{ viewerSource, openSource, setPage, closeViewer }}>
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
