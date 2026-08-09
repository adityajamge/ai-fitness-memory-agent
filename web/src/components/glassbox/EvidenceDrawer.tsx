/**
 * The evidence pane on mobile/tablet — DESIGN.md §5.8, §9 (amended 2026-08-09, §16 Decisions Log).
 *
 * Below `lg` there is no honest way to show three panes side by side, so the engine becomes a
 * drawer instead of a fixed column — opened either by the citation gesture or by the same handle
 * that collapses the desktop pane (`AppScreen`'s toggle). **Slides in from the right**, mirroring
 * where the pane lives on desktop, rather than the bottom sheet this used to be — explicit
 * instruction, since "the engine becomes a bottom drawer" was itself a considered §5.8 decision
 * (a half-height sheet above the mobile keyboard was the original reasoning; that trade-off is
 * gone now, not forgotten).
 *
 * Base UI `Drawer` rather than `vaul`: vaul is unmaintained, and mixing it in would add a second
 * portal/focus system next to our Dialog with documented conflicts (DESIGN.md §11). Positioning a
 * side drawer isn't a built-in variant — Base UI drives it entirely through the consumer's own
 * CSS (`swipeDirection` only controls the swipe-to-dismiss gesture) — so `Drawer.Popup` here is
 * positioned exactly the way `EvidencePane`'s desktop column is: `fixed`, full height, docked
 * right, `border-l`.
 */

import { Drawer } from "@base-ui/react/drawer";
import { X } from "lucide-react";
import { memo } from "react";
import type { EvidenceTrace, MemoryRow } from "@/api/schemas";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { EvidencePaneBody } from "./EvidencePane";

export interface EvidenceDrawerProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  trace: EvidenceTrace | null;
  rows: Map<string, MemoryRow>;
  isHydrating: boolean;
  missingCount: number;
  hasTurns: boolean;
  activeId: string | null;
}

/** Memoized for the same reason as `EvidencePane` — mobile's equivalent of the desktop column,
 * with none of its props tied to the composer draft. */
export const EvidenceDrawer = memo(function EvidenceDrawer({
  isOpen,
  onOpenChange,
  ...body
}: EvidenceDrawerProps) {
  return (
    <Drawer.Root open={isOpen} onOpenChange={onOpenChange} swipeDirection="right">
      <Drawer.Portal>
        <Drawer.Backdrop className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px] transition-opacity duration-medium ease-move data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Drawer.Popup
          className={cn(
            "fixed inset-y-0 right-0 z-50 flex h-dvh w-pane max-w-[88vw] flex-col",
            "border-l border-border bg-surface shadow-(--shadow-overlay) focus:outline-none",
            "transition-transform duration-medium ease-move",
            "data-ending-style:translate-x-full data-starting-style:translate-x-full",
          )}
        >
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-3">
            <Drawer.Title className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
              Memory Engine
            </Drawer.Title>
            <Drawer.Close
              render={
                <Button variant="ghost" size="icon" aria-label="Close evidence" className="ml-auto">
                  <X className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </Button>
              }
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            <EvidencePaneBody {...body} />
          </div>
        </Drawer.Popup>
      </Drawer.Portal>
    </Drawer.Root>
  );
});
