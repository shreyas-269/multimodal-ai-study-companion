import type { SourceItem } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button, buttonVariants } from "@/components/ui/button";

const KIND_LABELS: Record<SourceItem["kind"], string> = {
  pdf: "PDF",
  slides_pdf: "Slides",
  pptx: "PowerPoint",
  docx: "Word",
  video: "Video",
  markdown: "Markdown",
  html: "Web page",
  xlsx: "Excel",
  web: "Website",
};

function formatDuration(durationS: number): string {
  const totalMinutes = Math.max(1, Math.round(durationS / 60));
  if (totalMinutes < 60) {
    return `${totalMinutes} min`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours} h ${minutes} min`;
}

function getStatusText(source: SourceItem): string | null {
  if (source.status === "ready") {
    return null;
  }
  if (source.status === "queued") {
    return "Queued";
  }
  if (source.status === "processing") {
    const stage = source.stage?.trim();
    return stage ? `Processing… (${stage})` : "Processing…";
  }
  if (source.status === "failed") {
    const err = source.error?.trim();
    return err ? `Failed: ${err}` : "Failed";
  }
  return null;
}

export function SourceCard({
  source,
  onOpenPdf,
}: {
  source: SourceItem;
  onOpenPdf: (sourceId: string, title: string) => void;
}) {
  const metaParts: string[] = [KIND_LABELS[source.kind]];
  if (source.role === "syllabus") {
    metaParts.push("Syllabus");
  }
  if (source.page_count != null) {
    metaParts.push(
      source.page_count === 1 ? "1 page" : `${source.page_count} pages`
    );
  }
  if (source.duration_s != null) {
    metaParts.push(formatDuration(source.duration_s));
  }

  const statusText = getStatusText(source);

  const trimmedLicence = source.licence?.trim() || null;
  const trimmedAttribution = source.attribution?.trim() || null;
  const hasLicenceOrAttribution = Boolean(trimmedLicence || trimmedAttribution);

  let action: React.ReactNode = null;
  if (source.status === "ready") {
    if (source.kind === "video") {
      if (source.youtube_url) {
        action = (
          <a
            href={source.youtube_url}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: "outline", size: "sm" })}
          >
            Watch on YouTube
          </a>
        );
      }
    } else {
      action = (
        <Button
          variant="outline"
          size="sm"
          onClick={() => onOpenPdf(source.id, source.title)}
        >
          Open
        </Button>
      );
    }
  }

  return (
    <Card className="p-4 flex flex-col justify-between gap-3 min-w-0">
      <div className="space-y-2 min-w-0">
        <h3 className="font-medium line-clamp-2" title={source.title}>
          {source.title}
        </h3>
        <p className="text-xs text-muted-foreground">{metaParts.join(" · ")}</p>
        {statusText && (
          <p className="text-xs text-muted-foreground">{statusText}</p>
        )}
        {hasLicenceOrAttribution && (
          <div className="space-y-0.5 text-xs text-muted-foreground">
            {trimmedLicence && <p>Licence: {trimmedLicence}</p>}
            {trimmedAttribution && <p>{trimmedAttribution}</p>}
          </div>
        )}
      </div>
      {action && <div className="mt-auto pt-1">{action}</div>}
    </Card>
  );
}
