"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@/components/auth-provider";
import { ApiError, getMe } from "@/lib/api";
import { Button, buttonVariants } from "@/components/ui/button";

interface AccountBarProps {
  compact?: boolean;
}

export function AccountBar({ compact = false }: AccountBarProps = {}) {
  const { user, loading, signOut } = useAuth();
  const router = useRouter();

  const [isSigningOut, setIsSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["me", user?.uid],
    queryFn: getMe,
    enabled: !loading && !!user,
  });

  const handleSignOut = async () => {
    setIsSigningOut(true);
    setSignOutError(null);
    try {
      await signOut();
      router.replace("/login");
    } catch (err) {
      setSignOutError(err instanceof Error ? err.message : "Failed to sign out");
      setIsSigningOut(false);
    }
  };

  const getStudyCoachStatus = (coach: boolean | null | undefined) => {
    if (coach == null) {
      return "Study Coach: not asked yet";
    }
    return coach ? "Study Coach: on" : "Study Coach: off";
  };

  if (compact) {
    const userLabel = data
      ? data.is_guest
        ? "Guest"
        : (data.email ?? "Unknown")
      : null;

    return (
      <div className="flex items-center gap-2">
        {isLoading && <span className="text-sm text-muted-foreground">Loading…</span>}

        {error && (
          <span className="text-sm text-muted-foreground truncate">
            {error instanceof ApiError ? error.message : "Failed to load user profile"}
          </span>
        )}

        {userLabel && (
          <span className="text-sm text-muted-foreground truncate">
            {userLabel}
          </span>
        )}

        <Link href="/settings" className={buttonVariants({ variant: "ghost", size: "sm" })}>
          Settings
        </Link>
        <Button variant="ghost" size="sm" onClick={handleSignOut} disabled={isSigningOut}>
          Sign out
        </Button>
        {signOutError && <span className="text-sm text-muted-foreground">{signOutError}</span>}
      </div>
    );
  }

  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 rounded-lg border p-4">
      <div className="space-y-1 text-sm">
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

      <div className="flex flex-col items-start sm:items-end gap-1">
        <div className="flex items-center gap-2">
          <Link href="/settings" className={buttonVariants({ variant: "outline" })}>
            Settings
          </Link>
          <Button variant="outline" onClick={handleSignOut} disabled={isSigningOut}>
            Sign out
          </Button>
        </div>
        {signOutError && <p className="text-sm text-muted-foreground">{signOutError}</p>}
      </div>
    </div>
  );
}
