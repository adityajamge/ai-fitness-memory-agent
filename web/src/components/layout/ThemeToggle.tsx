/**
 * Light/dark switch — DESIGN.md §16, added 2026-08-09.
 *
 * One icon button, everywhere the product has a persistent header (`TopBar`, the landing page's
 * nav). Not a three-way "system/light/dark" menu: `theme/themeStore.ts` already follows the OS
 * preference until someone touches this button, which covers "system" without a third menu item
 * to build and test.
 */

import { Moon, Sun } from "lucide-react";
import { toggleTheme, useTheme } from "@/theme/themeStore";
import { Button } from "@/components/ui/Button";

export function ThemeToggle({ className }: { className?: string }) {
  const theme = useTheme();
  const isDark = theme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className={className}
    >
      {isDark ? (
        <Moon className="size-4" strokeWidth={1.5} aria-hidden="true" />
      ) : (
        <Sun className="size-4" strokeWidth={1.5} aria-hidden="true" />
      )}
    </Button>
  );
}
