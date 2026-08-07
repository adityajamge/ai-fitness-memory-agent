/**
 * Field — a labelled input, DESIGN.md §6.2.
 *
 * Label + input + hint/error as one unit, because they are only ever correct together: a label
 * that is not `htmlFor`-bound, or an error that is not `aria-describedby`-bound, is invisible to
 * a screen reader while looking perfectly fine on screen. Binding them here means no call site
 * can get it wrong.
 *
 * **Labels are always rendered.** Placeholder-as-label is banned by the contract: a placeholder
 * disappears at exactly the moment it is needed — when the field has content and the user is
 * checking what they typed.
 */

import { useId, type InputHTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface FieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  /** Shown in --invalid beneath the field, and bound via aria-describedby. */
  error?: string | undefined;
  /** Quiet helper text. Suppressed while an error is showing so the two never stack. */
  hint?: string | undefined;
  /** Rendered inside the field, right-aligned — used for the password show/hide toggle. */
  trailing?: ReactNode;
}

export function Field({ label, error, hint, trailing, className, ...props }: FieldProps) {
  const id = useId();
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className="font-mono text-micro uppercase tracking-[0.08em] text-faint"
      >
        {label}
      </label>

      <div className="relative">
        <input
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy}
          className={cn(
            "h-10 w-full rounded-sm border bg-surface px-3 text-body text-foreground",
            "placeholder:text-faint",
            "transition-colors duration-120 ease-out",
            "focus:border-border-strong focus:outline-none",
            "disabled:opacity-50",
            trailing ? "pr-10" : undefined,
            error ? "border-invalid" : "border-border",
            className,
          )}
          {...props}
        />
        {trailing && (
          <div className="absolute inset-y-0 right-1 flex items-center">{trailing}</div>
        )}
      </div>

      {error ? (
        // role="alert" so the message is announced when it appears, not only when focus
        // happens to land back on the field.
        <p id={`${id}-error`} role="alert" className="text-meta text-invalid">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-meta text-faint">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
