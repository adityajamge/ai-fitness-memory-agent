/**
 * Top bar — DESIGN.md §6.13. 56px, and the product's entire navigation.
 *
 * **Three destinations** (2026-08-13 IA revision, §16 Decisions Log): Chat, Review, Profile — up
 * from two. **No account controls live here**: Sign out moved into Profile's own Account section
 * (§6.19), since Profile is a primary destination now rather than something reached through a
 * popover. **No memory/day/insight stats here either** (moved the same revision): the mono counts
 * that used to sit in this bar now open Review's state line (§6.20) — ambient database counts on
 * every screen, including Chat, was exactly the kind of always-on dashboard furniture this
 * revision removes. This bar is nav and connection status only.
 *
 * `useStats()` is still called here, but only for the connection indicator's error signal — the
 * visible count readout it used to feed is gone.
 */

import { Link, useLocation } from "react-router";
import { useStats } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "./ThemeToggle";
import { cn } from "@/lib/utils";

/** Three dots showing engine activity. The bar's one ornament, and it is functional. */
function ConnectionDots({ isBusy, isError }: { isBusy: boolean; isError: boolean }) {
  return (
    <span
      className="ml-1 inline-flex items-center gap-0.75"
      role="status"
      aria-label={
        isError ? "Database unreachable" : isBusy ? "Query running" : "Database connected"
      }
    >
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className={cn(
            "size-1 rounded-full transition-colors duration-240",
            isError ? "bg-invalid" : isBusy ? "bg-signal" : "bg-faint",
          )}
          style={isBusy ? { animation: `pulse 1s ${i * 0.15}s infinite` } : undefined}
        />
      ))}
    </span>
  );
}

export interface TopBarProps {
  isBusy?: boolean;
}

export function TopBar({ isBusy = false }: TopBarProps) {
  const { pathname } = useLocation();
  // Only `isError` is read now — the connection indicator's signal. The visible count readout
  // this query used to feed lives in Review's state line instead (§6.20).
  const { isError } = useStats();

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4 md:px-6">
      <Link to="/app" aria-label="AyuMind AI — Chat" className="flex items-center gap-2">
        <Logo size={24} glowStrength={0} />
        <span className="hidden text-dense font-medium text-foreground sm:inline">
          AyuMind AI
        </span>
      </Link>

      {/* Three surfaces, and that is the entire primary navigation (2026-08-13 IA revision). The
          research's read of the category is that a mature product carries three to five and
          Oura shipped a redesign *removing* two — three is where this product stops until a
          surface earns a fourth. Underline, not a filled pill: the bar is 56px and a chip row at
          this height reads as chrome competing with the connection status beside it. */}
      <nav aria-label="Primary" className="ml-2 flex items-center gap-1 md:ml-4">
        {(
          [
            ["/app", "Chat"],
            ["/app/review", "Review"],
            ["/app/profile", "Profile"],
          ] as const
        ).map(([to, label]) => {
          const isActive = pathname === to;
          return (
            <Link
              key={to}
              to={to}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "rounded-xs border-b px-2 py-1 text-dense transition-colors duration-120",
                "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal",
                isActive
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-4">
        <span className="hidden items-center gap-1.5 font-mono text-meta text-faint sm:flex">
          Database
          <ConnectionDots isBusy={isBusy} isError={isError} />
        </span>

        <ThemeToggle />
      </div>
    </header>
  );
}
