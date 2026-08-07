/**
 * Session-expiry notice and re-auth — DESIGN.md §6.11.1 (F-T4).
 *
 * Three things hold, and each is a deliberate refusal of the conventional behavior:
 *
 * 1. **No redirect.** A redirect to /login discards the typed message. Never losing what you told
 *    it is this product's promise (ADR-13.5), and the auth boundary does not get an exemption.
 * 2. **The conversation stays on screen**, read-only. Clearing it reads as a crash.
 * 3. **Re-auth happens over the app**, in a dialog, so nothing moves and nothing is lost.
 *
 * On success the notice disappears, queries refetch, and the composer's draft is still there —
 * exactly where the user left it.
 */

import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Dialog } from "@base-ui/react/dialog";
import { ApiError, login as loginRequest } from "@/api/client";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { clearSessionExpired } from "./sessionStore";

export function SessionNotice() {
  const queryClient = useQueryClient();
  const [isOpen, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string>();
  const [isSubmitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError(undefined);
    setSubmitting(true);
    try {
      await loginRequest(email, password);
      clearSessionExpired();
      setOpen(false);
      setPassword("");
      // Everything the app shows was fetched under a dead session; refetch it all rather than
      // leaving stale-but-plausible numbers on screen.
      await queryClient.invalidateQueries();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 401
          ? "email or password is incorrect"
          : "couldn't reach the server",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div
        role="status"
        className="flex items-center gap-2 border-t border-border bg-surface-2 px-4 py-2 text-meta text-muted-foreground"
      >
        <span className="size-1.5 shrink-0 rounded-full bg-invalid" aria-hidden="true" />
        <span>your session ended</span>
        <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
          sign in to continue
        </Button>
        <span className="ml-auto hidden font-mono text-faint sm:inline">
          your message is safe
        </span>
      </div>

      <Dialog.Root open={isOpen} onOpenChange={setOpen}>
        <Dialog.Portal>
          <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px]" />
          <Dialog.Popup className="fixed left-1/2 top-1/2 z-50 w-[min(420px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-surface-3 p-6 shadow-[var(--shadow-overlay)]">
            <Dialog.Title className="text-h3 text-foreground">Sign back in</Dialog.Title>
            <Dialog.Description className="mt-1 text-dense text-muted-foreground">
              Your conversation and anything you typed are still here.
            </Dialog.Description>

            <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-4" noValidate>
              <Field
                label="Email"
                type="email"
                autoComplete="email"
                autoFocus
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <Field
                label="Password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                error={error}
              />
              <Button type="submit" variant="primary" size="lg" isLoading={isSubmitting}>
                Sign in
              </Button>
            </form>
          </Dialog.Popup>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
