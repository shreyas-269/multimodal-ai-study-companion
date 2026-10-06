import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "Page not found",
};

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl font-semibold tracking-tight">
            Page not found
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground">
            There&apos;s nothing at this address.
          </p>
          <div className="pt-2">
            <Link
              href="/notebooks"
              className={buttonVariants({ className: "w-full" })}
            >
              Go to your notebooks
            </Link>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
