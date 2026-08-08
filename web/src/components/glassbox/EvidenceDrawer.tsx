/**
 * The evidence pane on mobile — DESIGN.md §5.8, §9.
 *
 * Below `lg` there is no honest way to show three panes, so the engine becomes a bottom drawer
 * that opens **on the same gesture that activates a citation chip**. That coupling is the point:
 * the claim→proof link is the product, and it is the last thing to be compromised on a small
 * screen.
 *
 * Base UI `Drawer` rather than `vaul`: vaul is unmaintained, and mixing it in would add a second
 * portal/focus system next to our Dialog with documented conflicts (DESIGN.md §11).
 */

import { Drawer } from "@base-ui/react/drawer";
import { X } from "lucide-react";
import type { EvidenceTrace, MemoryRow } from "@/api/schemas";
import { Button } from "@/components/ui/Button";
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

export function EvidenceDrawer({
  isOpen,
  onOpenChange,
  ...body
}: EvidenceDrawerProps) {
  return (
    <Drawer.Root open={isOpen} onOpenChange={onOpenChange}>
      <Drawer.Portal>
        <Drawer.Backdrop className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px]" />
        <Drawer.Popup className="fixed inset-x-0 bottom-0 z-50 flex max-h-[85dvh] flex-col rounded-t-lg border-t border-border bg-surface shadow-[var(--shadow-overlay)] focus:outline-none">
          {/* A real grab handle: Base UI's SwipeArea makes the drag gesture work rather than
              just drawing an affordance that does nothing. */}
          <Drawer.SwipeArea className="flex shrink-0 justify-center py-2">
            <span aria-hidden="true" className="h-1 w-9 rounded-full bg-border-strong" />
          </Drawer.SwipeArea>

          <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 pb-3">
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
}
