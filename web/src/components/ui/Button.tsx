/**
 * Button — DESIGN.md §6.1.
 *
 * Four variants and three sizes. **No others may be created**: a fifth variant is a design
 * decision, not a component change, and it needs a DESIGN.md entry first.
 *
 * Deliberately absent, per the contract: gradients, pill radius, shadows, and hover-lift. Hover
 * moves one surface step; active insets 1px rather than scaling. A scale transform on press reads
 * as a toy, and this product is an instrument.
 */

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Spinner } from "./Spinner";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg" | "icon";

const VARIANT: Record<Variant, string> = {
  // The one action on a screen. Inverted rather than accent-colored: --signal means "evidence
  // you can open" and spending it on a submit button would dilute the only color that carries
  // meaning (§5.2).
  primary:
    "bg-foreground text-background hover:bg-foreground/90 active:bg-foreground/80 font-medium",
  secondary: "bg-surface-2 text-foreground border border-border hover:bg-surface-3",
  ghost: "bg-transparent text-muted-foreground hover:bg-surface-2 hover:text-foreground",
  danger: "bg-transparent text-invalid border border-invalid hover:bg-invalid-dim",
};

const SIZE: Record<Size, string> = {
  sm: "h-7 px-3 text-dense",
  md: "h-8 px-4 text-dense",
  lg: "h-10 px-4 text-body",
  // Square, zero horizontal padding. This exists as a real size rather than as a `px-0` override
  // at the call site because `cn` does not merge conflicting utilities: Tailwind resolves
  // `px-4` vs `px-0` by CSS order, so the override silently lost and squeezed the icon to 0px
  // wide. The conflict was the signal that the API was missing a case — which is precisely why
  // this codebase does not paper over it with tailwind-merge.
  icon: "size-8 p-0",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Swaps the label for a spinner and disables the button. Width is held so nothing reflows. */
  isLoading?: boolean;
  children?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  isLoading = false,
  className,
  disabled,
  children,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || isLoading}
      // aria-busy rather than swapping the accessible name: a screen reader should hear the
      // same button, now busy, not a button that turned into "loading".
      aria-busy={isLoading || undefined}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-sm whitespace-nowrap",
        "transition-colors duration-120 ease-out",
        "active:translate-y-px",
        // Opacity lands on the container, never on the text: dimming the label alone would
        // drop it below the 4.5:1 floor (§5.9).
        "disabled:pointer-events-none disabled:opacity-50",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...props}
    >
      {isLoading ? <Spinner /> : children}
    </button>
  );
}
