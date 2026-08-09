/**
 * 404 — DESIGN.md §6.11, route-level error state.
 *
 * A real one, because §8.5 lists it among the premium details that actually matter: a default
 * framework 404 is the fastest way to tell a visitor that nobody finished the edges.
 */

import { Link } from "react-router";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";

export function NotFound() {
  return (
    <main className="flex min-h-dvh items-center px-6 md:px-[max(6vw,48px)]">
      <div className="max-w-[52ch]">
        <Logo size={20} animated={false} glowStrength={0} />
        <p className="mt-8 font-mono text-micro uppercase tracking-[0.08em] text-faint">404</p>
        <h1 className="mt-2 text-h2 text-foreground">There's nothing at this address.</h1>
        <p className="mt-3 text-body text-muted-foreground">
          The page you asked for doesn't exist. Your memory is untouched.
        </p>
        <div className="mt-8">
          <Link to="/">
            <Button variant="secondary" size="lg">Back to the start</Button>
          </Link>
        </div>
      </div>
    </main>
  );
}
