/**
 * Top bar — DESIGN.md §6.13. 56px, and the product's entire navigation.
 *
 * Deliberately navigation-light. It carries **two** product surfaces — Today and Chat — and
 * nothing else; Profile stays a text button on the right, where MyFitnessPal's 2026 redesign
 * also moved its overflow menu. Inventing a tab bar beyond that would be the "dashboard"
 * mistake wireframe v2 was rejected for, in better manners.
 *
 * The stats are mono because they came out of CockroachDB (§4.1). "4,182 memories · 312 days ·
 * 23 insights" says *memory system* before a word of the conversation is read, which is the
 * bar's real job.
 */

import { Link, useLocation, useNavigate } from "react-router";
import { logout } from "@/api/client";
import { useStats } from "@/api/queries";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
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
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { data, isPending, isError } = useStats();

  async function handleSignOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-border px-4 md:px-6">
      <div className="flex items-center gap-2">
        <Logo size={24} glowStrength={0} />
        <span className="hidden text-dense font-medium text-foreground sm:inline">
          AyuMind AI
        </span>
      </div>

      {/* Two surfaces, and that is the entire primary navigation. The research's read of the
          category is that a mature product carries three to five and Oura shipped a redesign
          *removing* two — inventing a tab bar here would be the wireframe-v2 mistake with
          better manners. Underline, not a filled pill: the bar is 56px and a chip row at this
          height reads as chrome competing with the stats beside it. */}
      <nav aria-label="Primary" className="ml-2 flex items-center gap-1 md:ml-4">
        {(
          [
            ["/app/today", "Today"],
            ["/app", "Chat"],
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
        {isPending ? (
          <Skeleton className="h-3.5 w-48" />
        ) : (
          // A <dl> rather than divs: these are label/value pairs, and a screen reader should
          // read them as such (§5.9).
          <dl className="hidden items-center gap-1.5 font-mono text-meta text-muted-foreground sm:flex">
            {/* A <dl> may only contain dt/dd groups (optionally wrapped in <div>). Two
                consequences, both load-bearing:
                  · DOM order is dt-then-dd, and CSS `order` flips it so it *reads*
                    "4,182 memories" without the invalid markup.
                  · The "·" separators are CSS `::after`, not elements. As <div>s they were a
                    serious axe violation, because a div in a <dl> is expected to wrap a group. */}
            {(
              [
                [data?.memories ?? 0, "memory", "memories"],
                [data?.days ?? 0, "day", "days"],
                [data?.insights ?? 0, "insight", "insights"],
              ] as const
            ).map(([count, one, many]) => (
              <div
                key={one}
                // `after:order-3` is required, not decorative: the pseudo-element is a flex item
                // and defaults to order 0, so without it the separator sorts *ahead* of the
                // order-1/order-2 children and renders before the number.
                className="flex items-center gap-1 not-last:after:order-3 not-last:after:ml-1.5 not-last:after:text-faint not-last:after:content-['·']"
              >
                {/* Pluralized: "1 memories · 1 days" is the kind of detail that makes a product
                    read as unfinished, and a first-run account always hits n=1. */}
                <dt className="order-2">{count === 1 ? one : many}</dt>
                <dd className="order-1 text-foreground">{count}</dd>
              </div>
            ))}
          </dl>
        )}

        <span className="hidden items-center gap-1.5 font-mono text-meta text-faint sm:flex">
          Database
          <ConnectionDots isBusy={isBusy} isError={isError} />
        </span>

        <ThemeToggle />

        <Button variant="ghost" size="sm" onClick={() => navigate("/app/profile")}>
          Profile
        </Button>
        <Button variant="ghost" size="sm" onClick={handleSignOut}>
          Sign out
        </Button>
      </div>
    </header>
  );
}
