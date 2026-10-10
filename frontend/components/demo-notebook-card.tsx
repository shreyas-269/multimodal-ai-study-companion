import Link from "next/link";
import type { NotebookItem } from "@/lib/api";
import { DEMO_NOTEBOOK_DESCRIPTION } from "@/lib/site";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface DemoNotebookCardProps {
  notebook: NotebookItem;
}

export function DemoNotebookCard({ notebook }: DemoNotebookCardProps) {
  return (
    <Card className="border-2 border-foreground/20">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-lg font-semibold">{notebook.name}</CardTitle>
          <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-semibold text-secondary-foreground">
            Demo
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {DEMO_NOTEBOOK_DESCRIPTION}
        </p>
        <div>
          <Link
            href={`/notebooks/${encodeURIComponent(notebook.id)}`}
            className={buttonVariants({ variant: "default" })}
          >
            Open the demo notebook
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
