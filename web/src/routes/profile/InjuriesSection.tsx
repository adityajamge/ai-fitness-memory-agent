/** Injuries / notes — free text, feeds agent context only (§6.19). `memo`'d, see `IdentitySection`. */

import { memo } from "react";

export interface InjuriesSectionProps {
  injuries: string;
  onInjuriesChange: (v: string) => void;
}

export const InjuriesSection = memo(function InjuriesSection({
  injuries,
  onInjuriesChange,
}: InjuriesSectionProps) {
  return (
    <section className="flex flex-col gap-2 border-t border-border pt-6">
      <h2 className="text-dense font-medium text-foreground">Injuries / notes</h2>
      <textarea
        value={injuries}
        onChange={(e) => onInjuriesChange(e.target.value)}
        rows={3}
        placeholder="anything AyuMind should know when talking about training or recovery"
        className="w-full rounded-sm border border-border bg-surface px-3 py-2 text-body text-foreground placeholder:text-faint focus:border-border-strong focus:outline-none"
      />
    </section>
  );
});
