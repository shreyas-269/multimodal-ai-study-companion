import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Notebook",
};

export default function NotebookLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
