"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";

type Size = "sm" | "md" | "wide" | "lg" | "xl";

const WIDTHS: Record<Size, string> = { sm: "sm:max-w-md", md: "sm:max-w-xl", wide: "sm:max-w-2xl", lg: "sm:max-w-4xl", xl: "sm:max-w-7xl" };

// A modal. On a phone it becomes a sheet anchored to the bottom edge (full width, rounded top,
// scrolls inside itself); from `sm` up it is a centered card. Use `lg` for forms and workflows
// that need room; small panels belong in the inspector instead.
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  size = "md",
  flush = false,
  children,
  footer,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  size?: Size;
  // No padding around the body, for content that lays itself out (a file browser).
  flush?: boolean;
  children: ReactNode;
  footer?: ReactNode;
}) {
  // The dialog is controlled and has no Radix trigger, so Radix has nothing to hand focus
  // back to. Remember what had focus when it opened and restore it on close.
  const opener = useRef<HTMLElement | null>(null);
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 animate-fade-in bg-scrim" />
        <RadixDialog.Content
          onOpenAutoFocus={() => { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
          onCloseAutoFocus={(event) => { event.preventDefault(); opener.current?.focus(); }}
          // Without a description there is nothing to point at; an explicit undefined silences
          // Radix's warning. With one, Radix wires the description itself.
          {...(description ? {} : { "aria-describedby": undefined })}
          className={cn(
            "fixed z-50 flex max-h-[92dvh] w-full flex-col border border-line bg-raised text-ink shadow-xl",
            "inset-x-0 bottom-0 animate-sheet-in rounded-t-card pb-[env(safe-area-inset-bottom)]",
            "sm:inset-auto sm:left-1/2 sm:top-1/2 sm:max-h-[85dvh] sm:w-[calc(100vw-2rem)] sm:-translate-x-1/2 sm:-translate-y-1/2 sm:animate-pop-in sm:rounded-card sm:pb-0",
            WIDTHS[size],
          )}
        >
          <header className="flex items-start gap-3 border-b border-line px-5 py-4">
            <div className="min-w-0 flex-1">
              <RadixDialog.Title className="truncate text-base font-semibold">{title}</RadixDialog.Title>
              {description ? (
                <RadixDialog.Description className="mt-0.5 text-sm text-muted">{description}</RadixDialog.Description>
              ) : null}
            </div>
            <RadixDialog.Close
              aria-label={t("common.close")}
              className="-mr-2 -mt-1 inline-flex size-9 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink touch:size-11"
            >
              <X size={18} aria-hidden="true" />
            </RadixDialog.Close>
          </header>
          <div className={cn("min-h-0 flex-1", flush ? "flex flex-col" : "overflow-y-auto px-5 py-4")}>{children}</div>
          {footer ? <footer className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
