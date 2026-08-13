import type { ReactNode } from "react";

/** The small mono field-group caption shared by every profile section (Sex, Units, Primary
 * goal, ...). Trivial enough not to need `memo` itself — it only ever receives static text. */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <span className="font-mono text-micro uppercase tracking-[0.08em] text-faint">
      {children}
    </span>
  );
}
