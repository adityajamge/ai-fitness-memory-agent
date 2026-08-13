/**
 * The recent-memory strip — proof, and the way into the rest of the history.
 *
 * It is deliberately **not a diary**. The engine caps it at eight rows, and the rows are not
 * editable, groupable, or filterable here: clicking one hands off to Chat's day view
 * (`AppScreen`'s `handleScrub`, reached via `navigate("/app", { state: { day } })`), so there is
 * exactly one place in the product where "everything logged on a day" is rendered. Building a
 * second would fork the definition of a day between two screens, which is how the timeline's
 * count and a diary's list start disagreeing.
 *
 * `type <> 'insight'` is enforced server-side (`glassbox.fetch_recent_memories`) and matters
 * here too: these are things the *user* reported. An engine-derived claim in this list would
 * tell someone they logged something they never said — the same separation `Receipt` draws
 * between `created` and `insights`.
 *
 * Provenance is a dashed outline vs a filled tag and confidence is a four-segment meter, exactly
 * as in `EvidenceRow`. Both survive grayscale, which is the point (§5.9, WCAG 1.4.1).
 */

import type { MemoryRow } from "@/api/schemas";
import { ConfidenceMeter } from "@/components/glassbox/EvidenceRow";
import { cn } from "@/lib/utils";

const WHEN = { day: "numeric", month: "short" } as const;
const TIME = { hour: "2-digit", minute: "2-digit" } as const;

export interface RecentMemoriesProps {
  memories: MemoryRow[];
  /** Hands off to Chat's day view — never opens a second diary surface. */
  onOpenDay: (day: string) => void;
}

export function RecentMemories({ memories, onOpenDay }: RecentMemoriesProps) {
  if (memories.length === 0) return null;

  return (
    <section className="flex flex-col gap-3" aria-labelledby="recent-memories">
      <h2
        id="recent-memories"
        className="font-mono text-micro uppercase tracking-[0.08em] text-faint"
      >
        Recently logged
      </h2>

      <ul className="flex flex-col gap-1">
        {memories.map((memory) => {
          const when = new Date(memory.event_time);
          const dayKey = memory.event_time.slice(0, 10);
          const isLive = memory.provenance === "live";
          return (
            <li key={memory.id}>
              <button
                type="button"
                onClick={() => onOpenDay(dayKey)}
                // 44px minimum touch target below 768px (rule 20) — `min-h-11` is 44px and is
                // dropped at `md`, where the pointer is precise and the extra height is padding
                // for nothing.
                className={cn(
                  "flex min-h-11 w-full items-center gap-3 rounded-md border border-transparent px-3 py-2 text-left",
                  "transition-colors duration-120 hover:border-border hover:bg-surface",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-signal md:min-h-0",
                )}
                aria-label={`${memory.summary ?? memory.type} — open ${dayKey}`}
              >
                {/* The receipt mark from §6.7. Not a green checkmark: the confirmation that
                    talking is logging is this glyph plus the mono detail beside it. */}
                <span aria-hidden="true" className="shrink-0 text-meta text-faint">
                  ✦
                </span>

                <span className="min-w-0 flex-1 truncate text-dense text-foreground">
                  {memory.summary ?? memory.type}
                </span>

                <span
                  className={cn(
                    "hidden shrink-0 rounded-xs px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.08em] sm:inline",
                    isLive
                      ? "bg-surface-3 text-muted-foreground"
                      : "border border-dashed border-border text-faint",
                  )}
                  title={
                    isLive
                      ? "captured as it happened"
                      : "rebuilt from records — the timestamp is an estimate"
                  }
                >
                  {memory.provenance}
                </span>

                <span className="hidden shrink-0 sm:inline">
                  <ConfidenceMeter value={memory.confidence} />
                </span>

                <time
                  dateTime={memory.event_time}
                  className="shrink-0 font-mono text-meta tabular-nums text-faint"
                >
                  {when.toLocaleDateString(undefined, WHEN)}
                  <span className="hidden md:inline">
                    {` ${when.toLocaleTimeString(undefined, TIME)}`}
                  </span>
                </time>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
