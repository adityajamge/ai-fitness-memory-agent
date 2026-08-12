/**
 * The thread sidebar as a self-contained rail — collapsible column on desktop, drawer below
 * `lg`, account row at the bottom (§6.21). Amended 2026-08-12 (§16 Decisions Log): extracted
 * out of `AppScreen` so `Today` can carry the exact same sidebar — ChatGPT's own layout, where
 * the conversation rail is a fixture of the whole product, not one screen of it. Today has no
 * conversation state of its own, so its `onSelectThread`/`onNewChat` just hand off to `/app`
 * (`session/threadStore.ts` + a navigation) — the same "Today has no machinery of its own"
 * pattern its composer and day-view clicks already use.
 *
 * Owns its own collapse/drawer/breakpoint state rather than lifting it to a shared store: each
 * mount (Today, Chat) gets independent state, so switching between the two screens does not
 * need to be threaded through anything global for a toggle this peripheral.
 */

import { useEffect, useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useThreads } from "@/api/queries";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { Sidebar } from "./Sidebar";
import { SidebarDrawer } from "./SidebarDrawer";

/** Matches the `lg` breakpoint every other collapsible-column/drawer split in this product
 * already uses (the evidence pane, and formerly the profile dialog/page split). */
const DESKTOP_QUERY = "(min-width: 1024px)";

export interface ThreadSidebarRailProps {
  activeThreadId: string;
  onSelectThread: (threadId: string) => void;
  onNewChat: () => void;
}

/** Renders inside a `relative` row alongside `<main>` (and, on `AppScreen`, the evidence pane) —
 * the toggle handle is `absolute`-positioned against that ancestor, mirroring the evidence
 * pane's own handle exactly (§16 Decisions Log: plain `calc()`, never `-translate-x-1/2`, which
 * measurably failed to hit-test correctly live). */
export function ThreadSidebarRail({
  activeThreadId,
  onSelectThread,
  onNewChat,
}: ThreadSidebarRailProps) {
  const threads = useThreads();
  const [isCollapsed, setCollapsed] = useState(false);
  const [isDrawerOpen, setDrawerOpen] = useState(false);
  const [isDesktop, setDesktop] = useState(
    () => typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches,
  );

  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY);
    const onChange = (e: MediaQueryListEvent) => setDesktop(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  /** A no-op re-click (the already-open thread) still closes the mobile drawer rather than
   * re-issuing the hand-off (mirrors `AppScreen.handleSelectThread`'s old behavior). */
  function handleSelect(id: string) {
    setDrawerOpen(false);
    if (id === activeThreadId) return;
    onSelectThread(id);
  }

  function handleNewChat() {
    setDrawerOpen(false);
    onNewChat();
  }

  const listProps = {
    threads: threads.data?.threads ?? [],
    isPending: threads.isPending,
    isError: threads.isError,
    activeThreadId,
    onSelect: handleSelect,
    onNewChat: handleNewChat,
    onRetry: () => void threads.refetch(),
  };

  return (
    <>
      {/* Sidebar column — same collapsible-fixed-width treatment as the evidence pane (§5.7):
          `w-sidebar` or `w-0`, `overflow-hidden` clipping the transition. */}
      <div
        className={cn(
          "hidden shrink-0 overflow-hidden border-r border-border transition-[width] duration-medium ease-move lg:block",
          isCollapsed ? "w-0" : "w-sidebar",
        )}
      >
        <div className="h-full w-sidebar">
          <Sidebar {...listProps} />
        </div>
      </div>

      <div
        className={cn(
          "absolute top-[calc(50%-16px)] z-10",
          "transition-[left] duration-medium ease-move",
          isDesktop && !isCollapsed ? "left-[calc(var(--container-sidebar)-16px)]" : "-left-4",
        )}
      >
        <Button
          type="button"
          variant="secondary"
          size="icon"
          onClick={() => (isDesktop ? setCollapsed((v) => !v) : setDrawerOpen((v) => !v))}
          aria-label={
            (isDesktop ? isCollapsed : !isDrawerOpen) ? "Show conversations" : "Hide conversations"
          }
          aria-expanded={isDesktop ? !isCollapsed : isDrawerOpen}
        >
          {(isDesktop ? isCollapsed : !isDrawerOpen) ? (
            <PanelLeftOpen className="size-4" strokeWidth={1.5} aria-hidden="true" />
          ) : (
            <PanelLeftClose className="size-4" strokeWidth={1.5} aria-hidden="true" />
          )}
        </Button>
      </div>

      {!isDesktop && (
        <SidebarDrawer isOpen={isDrawerOpen} onOpenChange={setDrawerOpen} {...listProps} />
      )}
    </>
  );
}
