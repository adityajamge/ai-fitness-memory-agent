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

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent, type KeyboardEvent } from "react";
import { ArrowUp, ImagePlus, X } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

const MAX_ROWS = 6;

//: Kept in lockstep with api/routers/chat.py's _PHOTO_CONTENT_TYPES / _PHOTO_MAX_BYTES.
const PHOTO_ACCEPT = "image/jpeg,image/png,image/webp";
const PHOTO_MAX_BYTES = 8 * 1024 * 1024;

export interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  /** Read-only, not disabled: the text stays visible and selectable while the session is gone. */
  isLocked?: boolean;
  isSending?: boolean;
  placeholder?: string;
  /** M7 (DESIGN.md §16 Decisions Log, 2026-08-14) — the photo attached to the next send, if
   * any. Lifted to the caller so it survives a failed send the same way `value` already does
   * (§6.11.1's "the draft survives a 401", extended to cover upload failure). */
  image?: File | null;
  onAttachImage?: (file: File) => void;
  onRemoveImage?: () => void;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  isLocked = false,
  isSending = false,
  placeholder = "Ask anything, or log it — meals, workouts, sleep, reports…",
  image = null,
  onAttachImage,
  onRemoveImage,
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [attachError, setAttachError] = useState<string | null>(null);
  const canSend = !isLocked && (value.trim().length > 0 || image != null);

  // Thumbnail from the File object; revoked whenever it changes or unmounts so we don't leak
  // blob URLs across attach/remove/resend cycles.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!image) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(image);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);

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
      if (canSend) onSubmit();
      return;
    }
    // §9 shortcuts: Esc blurs the composer. Overlays (Dialog/Drawer) close on Esc already, as
    // Base UI's own behavior — this is only the "blur composer" half.
    if (event.key === "Escape") {
      ref.current?.blur();
    }
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (canSend) onSubmit();
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = ""; // let re-attaching the same file re-fire onChange
    if (!file) return;
    if (file.size > PHOTO_MAX_BYTES) {
      setAttachError(`Image too large — max ${PHOTO_MAX_BYTES / (1024 * 1024)}MB.`);
      return;
    }
    setAttachError(null);
    onAttachImage?.(file);
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-2 rounded-md border border-border bg-surface p-2 transition-colors duration-120 focus-within:border-border-strong"
    >
      {image && (
        <div className="flex items-center gap-2 rounded-sm border border-border bg-surface-2 p-1.5">
          {previewUrl && (
            <img
              src={previewUrl}
              alt=""
              className="size-10 shrink-0 rounded-sm object-cover"
            />
          )}
          <span className="min-w-0 flex-1 truncate text-dense text-muted-foreground">
            {image.name}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => {
              setAttachError(null);
              onRemoveImage?.();
            }}
            aria-label="Remove photo"
            className="shrink-0 size-6"
          >
            <X className="size-3.5" strokeWidth={1.5} aria-hidden="true" />
          </Button>
        </div>
      )}
      {attachError && <p className="px-1 text-dense text-invalid">{attachError}</p>}
      <div className="flex items-end gap-2">
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
        <input
          ref={fileInputRef}
          type="file"
          accept={PHOTO_ACCEPT}
          capture="environment"
          onChange={handleFileChange}
          className="sr-only"
          tabIndex={-1}
        />
        <Button
          type="button"
          variant="ghost"
          size="icon"
          disabled={isLocked}
          onClick={() => fileInputRef.current?.click()}
          aria-label="Attach a food photo"
          className="shrink-0"
        >
          <ImagePlus className="size-4" strokeWidth={1.5} aria-hidden="true" />
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="icon"
          // Never disabled for `isSending` — only for an empty draft+photo or a dead session.
          disabled={!canSend}
          aria-label="Send message"
          className="shrink-0"
        >
          {isSending ? (
            <span className="size-1.5 animate-pulse rounded-full bg-background" />
          ) : (
            <ArrowUp className="size-4" strokeWidth={2} aria-hidden="true" />
          )}
        </Button>
      </div>
    </form>
  );
}
