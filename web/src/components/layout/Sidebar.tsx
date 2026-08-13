/**
 * The thread sidebar — DESIGN.md §13 (added 2026-08-09, un-deferring "multi-thread conversation
 * UI" — see §16 Decisions Log), explicit instruction. **Scoped to Chat only** since the
 * 2026-08-13 IA revision (§6.21): it no longer renders on Review or Profile, and no longer
 * carries an account menu — Sign out moved into Profile's own Account section once Profile
 * became a primary nav destination.
 *
 * `ThreadList` is the shared body — "New chat" and the actual list of conversations — used by
 * both the desktop column (`Sidebar`, below) and the mobile/tablet drawer (`SidebarDrawer`), the
 * same split `EvidencePane`/`EvidenceDrawer` already establish for the engine pane. Fixed width,
 * never flexes (`--container-sidebar`), for the same reason the engine pane doesn't: a list of
 * short labels has no use for extra width on a wide monitor.
 */

import { MessageSquarePlus } from "lucide-react";
import type { ThreadSummary } from "@/api/schemas";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { cn } from "@/lib/utils";

function formatThreadDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export interface ThreadListProps {
  threads: ThreadSummary[];
  isPending: boolean;
  isError: boolean;
  activeThreadId: string;
  onSelect: (threadId: string) => void;
  onNewChat: () => void;
  onRetry: () => void;
}

export function ThreadList({
  threads,
  isPending,
  isError,
  activeThreadId,
  onSelect,
  onNewChat,
  onRetry,
}: ThreadListProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border p-2">
        <Button variant="secondary" size="md" onClick={onNewChat} className="w-full justify-start">
          <MessageSquarePlus className="size-4" strokeWidth={1.5} aria-hidden="true" />
          New chat
        </Button>
      </div>

      <nav aria-label="Conversation history" className="min-h-0 flex-1 overflow-y-auto p-2">
        {isPending ? (
          <div className="flex flex-col gap-2 p-1">
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-11 w-full" />
          </div>
        ) : isError ? (
          <div className="flex flex-col items-start gap-2 p-2 text-meta text-faint">
            couldn&apos;t load your conversations
            <button
              type="button"
              onClick={onRetry}
              className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
            >
              retry
            </button>
          </div>
        ) : threads.length === 0 ? (
          <p className="p-2 text-meta text-faint">Past conversations will appear here.</p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {threads.map((thread) => {
              const isActive = thread.thread_id === activeThreadId;
              return (
                <li key={thread.thread_id}>
                  <button
                    type="button"
                    onClick={() => onSelect(thread.thread_id)}
                    aria-current={isActive ? "true" : undefined}
                    className={cn(
                      "flex w-full flex-col items-start gap-0.5 rounded-sm px-2.5 py-2 text-left transition-colors duration-120",
                      isActive
                        ? "bg-surface-2 text-foreground"
                        : "text-muted-foreground hover:bg-surface-2 hover:text-foreground",
                    )}
                  >
                    <span className="line-clamp-1 w-full text-dense">{thread.preview}</span>
                    <span className="font-mono text-micro text-faint">
                      {formatThreadDate(thread.last_message_at)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>
    </div>
  );
}

/** Desktop shell: fixed `--container-sidebar` column, collapsible (`AppScreen` owns the state,
 * same as the engine pane) rather than a permanent fixture — mirrors §5.7's pane treatment. */
export function Sidebar(props: ThreadListProps) {
  return (
    <aside aria-label="Conversations" className="flex h-full flex-col bg-surface">
      <ThreadList {...props} />
    </aside>
  );
}
