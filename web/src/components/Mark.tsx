/**
 * The brand mark — DESIGN.md §2.
 *
 * A filled square holding a hairline inset square: a data cell containing a data cell. Two-tier
 * memory (episodic events + derived insights), rendered as a glyph.
 *
 * `interactive` adds the one playful detail in the product: the inner square rotates 90° on
 * hover, once, in the nav. Gated on reduced motion like everything else.
 */

import { cn } from "@/lib/utils";

export function Mark({
  size = 16,
  interactive = false,
  className,
}: {
  size?: number;
  interactive?: boolean;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={cn("shrink-0", interactive && "group", className)}
      aria-hidden="true"
    >
      <rect x="2" y="2" width="28" height="28" rx="2" className="fill-signal" />
      <rect
        x="12.5"
        y="12.5"
        width="7"
        height="7"
        rx="0.5"
        className={cn(
          "origin-center fill-none stroke-background",
          interactive &&
            "transition-transform duration-240 ease-move motion-safe:group-hover:rotate-90",
        )}
        strokeWidth="2"
      />
    </svg>
  );
}
