"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/components/auth-provider";
import { ApiError, getMe } from "@/lib/api";
import { Button } from "@/components/ui/button";

export default function NotebooksPage() {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();

  const { data, isLoading, error } = useQuery({
    queryKey: ["me", user?.uid],
    queryFn: getMe,
    enabled: !loading && !!user,
  });

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading) {
    return (
      <main className="p-8">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  if (!user) {
    return null;
  }

  const handleSignOut = async () => {
    await signOut();
    router.replace("/login");
  };

  const getStudyCoachStatus = (coach: boolean | null | undefined) => {
    if (coach == null) {
      return "Study Coach: not asked yet";
    }
    return coach ? "Study Coach: on" : "Study Coach: off";
  };

  return (
    <main className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Notebooks</h1>
          <p className="mt-1 text-sm text-muted-foreground">Your notebooks will appear here.</p>
        </div>
        <Button variant="outline" onClick={handleSignOut}>
          Sign out
        </Button>
      </div>

      <div className="rounded-lg border p-4 space-y-2">
        {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}

        {error && (
          <p className="text-sm text-muted-foreground">
            {error instanceof ApiError ? error.message : "Failed to load user profile"}
          </p>
        )}

        {data && (
          <div className="space-y-1 text-sm">
            <p className="font-medium">
              {data.is_guest ? "Signed in as Guest" : `Signed in as ${data.email ?? "Unknown"}`}
            </p>
            <p className="text-muted-foreground">{data.is_guest ? "Guest: yes" : "Guest: no"}</p>
            <p className="text-muted-foreground">{getStudyCoachStatus(data.study_coach)}</p>
          </div>
        )}
      </div>
    </main>
  );
}
