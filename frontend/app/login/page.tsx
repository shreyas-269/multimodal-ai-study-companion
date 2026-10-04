"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/components/auth-provider";
import { getAuthErrorMessage } from "@/lib/auth-errors";

export default function LoginPage() {
  const { user, loading, signIn, createAccount, continueAsGuest } = useAuth();
  const router = useRouter();
  const formRef = useRef<HTMLFormElement>(null);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!loading && user) {
      router.replace("/notebooks");
    }
  }, [loading, user, router]);

  if (loading || user) {
    return (
      <main className="flex min-h-screen items-center justify-center p-4">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    );
  }

  const handleSignIn = async (e: FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);
    setIsSubmitting(true);
    try {
      await signIn(email, password);
    } catch (err: unknown) {
      setErrorMessage(getAuthErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCreateAccount = async () => {
    if (!formRef.current?.reportValidity()) {
      return;
    }
    setErrorMessage(null);
    setIsSubmitting(true);
    try {
      await createAccount(email, password);
    } catch (err: unknown) {
      setErrorMessage(getAuthErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleContinueAsGuest = async () => {
    setErrorMessage(null);
    setIsSubmitting(true);
    try {
      await continueAsGuest();
    } catch (err: unknown) {
      setErrorMessage(getAuthErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Study Companion</CardTitle>
        </CardHeader>
        <CardContent>
          <form ref={formRef} onSubmit={handleSignIn} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isSubmitting}
              />
            </div>
            <div className="flex flex-col gap-2 pt-2">
              <Button type="submit" disabled={isSubmitting}>
                Sign in
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={isSubmitting}
                onClick={handleCreateAccount}
              >
                Create account
              </Button>
            </div>
          </form>

          <div className="mt-4 pt-4 border-t">
            <Button
              type="button"
              variant="ghost"
              className="w-full"
              disabled={isSubmitting}
              onClick={handleContinueAsGuest}
            >
              Continue as guest
            </Button>
          </div>

          {errorMessage && (
            <p className="mt-4 text-sm text-muted-foreground">{errorMessage}</p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
