import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Notebooks",
};

export default function NotebooksLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
