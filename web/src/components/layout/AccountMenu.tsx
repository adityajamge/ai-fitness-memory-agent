/**
 * The account entry point — DESIGN.md §6.21. Amended 2026-08-12 (§16 Decisions Log): moved out
 * of the top bar entirely (where it briefly lived as a circular icon, same day) to the bottom of
 * the left sidebar, ChatGPT/Claude-style — explicit user instruction, with a screenshot of
 * ChatGPT's own sidebar footer as the reference.
 *
 * One row: a circular identity glyph + the account's display name (or a plain "Account" label
 * before onboarding sets one). Clicking it opens a small menu **above** the row — it sits at the
 * bottom of its container, so the menu has nowhere else to open — with exactly two items:
 * Settings (Profile & goals, §6.19) and Sign out. Sign out no longer has its own top-bar button;
 * this is its only home now.
 */

import { CircleUserRound, LogOut, Settings } from "lucide-react";
import { Menu } from "@base-ui/react/menu";
import { useLocation, useNavigate } from "react-router";
import { logout } from "@/api/client";
import { useProfile } from "@/api/queries";
import { cn } from "@/lib/utils";

/** Matches the `lg` breakpoint every other collapsible/dialog-vs-drawer split in this product
 * already uses (AppScreen's evidence pane and sidebar, TopBar's own profile-open logic). */
const DESKTOP_QUERY = "(min-width: 1024px)";

export interface AccountMenuProps {
  /** The sidebar variant sits inside a bordered column and wants a top divider; the
   * drawer/standalone variant (Today, mobile) does not — it is already its own bottom fixture. */
  bordered?: boolean;
}

export function AccountMenu({ bordered = true }: AccountMenuProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const profile = useProfile();

  async function handleSignOut() {
    await logout();
    navigate("/login", { replace: true });
  }

  // Same device split as every other Profile entry point (§6.19): desktop opens it as a dialog
  // over the current screen, carrying the background location; below `lg` there is no honest
  // way to float a dialog over a phone screen, so it navigates plainly to the full page.
  function handleOpenSettings() {
    const isDesktop = typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches;
    navigate("/app/profile", isDesktop ? { state: { backgroundLocation: location } } : undefined);
  }

  const label = profile.data?.display_name || "Account";

  return (
    <Menu.Root>
      <Menu.Trigger
        className={cn(
          "flex w-full shrink-0 items-center gap-2 px-3 py-2.5 text-left transition-colors duration-120 ease-out",
          "hover:bg-surface-2 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-signal",
          bordered && "border-t border-border",
        )}
      >
        <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-full border border-border bg-surface-2 text-muted-foreground">
          <CircleUserRound className="size-4" strokeWidth={1.5} aria-hidden="true" />
        </span>
        <span className="truncate text-dense text-foreground">{label}</span>
      </Menu.Trigger>

      <Menu.Portal>
        <Menu.Positioner side="top" align="start" sideOffset={4} className="outline-none">
          <Menu.Popup className="min-w-40 rounded-md border border-border bg-surface-3 p-1 shadow-(--shadow-popover) outline-none">
            <Menu.Item
              onClick={handleOpenSettings}
              className="flex cursor-pointer items-center gap-2 rounded-sm px-2.5 py-2 text-dense text-foreground outline-none data-highlighted:bg-surface-2"
            >
              <Settings className="size-4" strokeWidth={1.5} aria-hidden="true" />
              Settings
            </Menu.Item>
            <Menu.Item
              onClick={() => void handleSignOut()}
              className="flex cursor-pointer items-center gap-2 rounded-sm px-2.5 py-2 text-dense text-foreground outline-none data-highlighted:bg-surface-2"
            >
              <LogOut className="size-4" strokeWidth={1.5} aria-hidden="true" />
              Sign out
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
