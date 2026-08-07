/**
 * The composer — DESIGN.md §6.2, "the most important input in the product".
 *
 * Two behaviors that are easy to get wrong and matter a lot:
 *
 * 1. **It is never disabled while a response streams.** The user can type the next message while
 *    the previous one is still working. Locking the input is the single most common way a chat UI
 *    feels slow even when it is not.
 * 2. **The draft survives a 401.** When the session expires the composer keeps its text and goes
 *    read-only, rather than being cleared or redirected away from (§6.11.1). Never losing what
 *    you told it is the product's promise, and the auth boundary is not exempt.
 */

import { useEffect, useRef, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

const MAX_ROWS = 6;

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** Read-only, not disabled: the text stays visible and selectable while the session is gone. */
  isLocked?: boolean;
  isSending?: boolean;
  placeholder?: string;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  isLocked = false,
  isSending = false,
  placeholder = "Ask anything, or log it — meals, workouts, sleep, reports…",
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow to MAX_ROWS. Measured from scrollHeight after a reset, because a textarea will not
  // shrink on its own once it has grown.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    const lineHeight = 24;
    const max = lineHeight * MAX_ROWS + 20;
    el.style.height = `${Math.min(el.scrollHeight, max)}px`;
  }, [value]);

  // `/` focuses the composer from anywhere (§9 shortcuts), but not while the user is already
  // typing in a field — otherwise the key never reaches the text they are writing.
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.isContentEditable;
      if (event.key === "/" && !typing) {
        event.preventDefault();
        ref.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter newlines. Cmd/Ctrl+Enter also sends, so the habit works either way.
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!isLocked && value.trim()) onSubmit();
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!isLocked && value.trim()) onSubmit();
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex items-end gap-2 rounded-md border border-border bg-surface p-2 transition-colors duration-120 focus-within:border-border-strong"
    >
      <label htmlFor="composer" className="sr-only">
        Message
      </label>
      <textarea
        id="composer"
        ref={ref}
        rows={1}
        value={value}
        readOnly={isLocked}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        className={cn(
          "max-h-[164px] flex-1 resize-none bg-transparent px-2 py-1.5 text-body",
          "text-foreground placeholder:text-faint focus:outline-none",
          isLocked && "opacity-60",
        )}
      />
      <Button
        type="submit"
        variant="primary"
        size="icon"
        // Never disabled for `isSending` — only for an empty draft or a dead session.
        disabled={isLocked || !value.trim()}
        aria-label="Send message"
        className="shrink-0"
      >
        {isSending ? (
          <span className="size-1.5 animate-pulse rounded-full bg-background" />
        ) : (
          <ArrowUp className="size-4" strokeWidth={2} aria-hidden="true" />
        )}
      </Button>
    </form>
  );
}
