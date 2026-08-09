/**
 * The thread sidebar on mobile/tablet — DESIGN.md §13 (added 2026-08-09, §16 Decisions Log).
 *
 * Mirrors `EvidenceDrawer.tsx` exactly, on the opposite edge: same reasoning (Base UI `Drawer`
 * has no built-in side variant, so `Drawer.Popup` is positioned by hand), same shape, just
 * `left`/`border-r`/`-translate-x-full` instead of `right`/`border-l`/`translate-x-full`.
 */

import { Drawer } from "@base-ui/react/drawer";
import { X } from "lucide-react";
import { memo } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { ThreadList, type ThreadListProps } from "./Sidebar";

export interface SidebarDrawerProps extends ThreadListProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}

export const SidebarDrawer = memo(function SidebarDrawer({
  isOpen,
  onOpenChange,
  ...body
}: SidebarDrawerProps) {
  return (
    <Drawer.Root open={isOpen} onOpenChange={onOpenChange} swipeDirection="left">
      <Drawer.Portal>
        <Drawer.Backdrop className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px] transition-opacity duration-medium ease-move data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Drawer.Popup
          className={cn(
            "fixed inset-y-0 left-0 z-50 flex h-dvh w-pane max-w-[88vw] flex-col",
            "border-r border-border bg-surface shadow-(--shadow-overlay) focus:outline-none",
            "transition-transform duration-medium ease-move",
            "data-ending-style:-translate-x-full data-starting-style:-translate-x-full",
          )}
        >
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
            <Drawer.Title className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
              Conversations
            </Drawer.Title>
            <Drawer.Close
              render={
                <Button variant="ghost" size="icon" aria-label="Close conversations" className="ml-auto">
                  <X className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </Button>
              }
            />
          </div>

          <div className="min-h-0 flex-1">
            <ThreadList {...body} />
          </div>
        </Drawer.Popup>
      </Drawer.Portal>
    </Drawer.Root>
  );
});
