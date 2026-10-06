"use client";

import { useEffect } from "react";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button, buttonVariants } from "@/components/ui/button";

export default function Error({
  error,
  retry,
  reset,
}: {
  error: Error & { digest?: string };
  retry: () => void;
  reset?: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const handleRetry = () => {
    if (typeof retry === "function") {
      retry();
    } else if (typeof reset === "function") {
      reset();
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl font-semibold tracking-tight">
            Something went wrong
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            The page hit an unexpected error. Trying again usually fixes it.
          </p>
          <div className="flex flex-col gap-2 pt-2">
            <Button type="button" onClick={handleRetry} className="w-full">
              Try again
            </Button>
            <Link
              href="/notebooks"
              className={buttonVariants({
                variant: "outline",
                className: "w-full",
              })}
            >
              Go to your notebooks
            </Link>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
