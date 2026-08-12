/**
 * Profile & goals, as a dialog — DESIGN.md §6.19/§6.5, ADR-17. Amended 2026-08-12 (§16
 * Decisions Log): on desktop the top bar's account icon opens this over the current screen
 * (chat/Today stays mounted underneath, per the background-location routing in `App.tsx`)
 * instead of navigating away from it. Mobile never renders this component — the same icon
 * navigates plainly to `/app/profile`, the full page in `ProfileSettings.tsx`.
 *
 * Both surfaces render the exact same `ProfileSettingsContent` — nothing here duplicates form
 * state or save logic, only the chrome around it.
 */

import { Dialog } from "@base-ui/react/dialog";
import { X } from "lucide-react";
import { useNavigate } from "react-router";
import { Button } from "@/components/ui/Button";
import { ProfileSettingsContent } from "./ProfileSettings";

export function ProfileSettingsDialog() {
  const navigate = useNavigate();

  // Closing (Esc, backdrop click, the X) steps back to the background location App.tsx pushed
  // this route's history entry on top of — chat/Today reappears exactly as it was, not a fresh
  // navigation to /app.
  function handleOpenChange(open: boolean) {
    if (!open) navigate(-1);
  }

  return (
    <Dialog.Root open onOpenChange={handleOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px] transition-opacity duration-medium ease-move data-ending-style:opacity-0 data-starting-style:opacity-0" />
        <Dialog.Popup
          // Enter: duration-enter + ease-out, per §6.5. Exit: same translate/opacity in
          // reverse, over duration-medium — data-ending-style overrides the base duration.
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-border bg-surface-3 shadow-(--shadow-overlay) transition-[opacity,transform] duration-enter ease-out focus:outline-none data-ending-style:translate-y-1 data-ending-style:opacity-0 data-ending-style:duration-medium data-starting-style:translate-y-1 data-starting-style:opacity-0"
        >
          <div className="flex shrink-0 items-center justify-between border-b border-border px-6 py-4">
            <Dialog.Title className="text-h3 text-foreground">Profile & goals</Dialog.Title>
            <Dialog.Close
              render={
                <Button variant="ghost" size="icon" aria-label="Close profile & goals">
                  <X className="size-4" strokeWidth={1.5} aria-hidden="true" />
                </Button>
              }
            />
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
            <ProfileSettingsContent />
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
