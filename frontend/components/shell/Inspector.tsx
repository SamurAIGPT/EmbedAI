"use client";

import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { useRef, type ReactNode } from "react";
import { t } from "@/lib/i18n";

const CLOSE = "inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink touch:size-11";

// The right-hand panel for something you look at beside the conversation: a computer, usage.
// Wide screens put it next to the chat; narrower ones slide it over the chat from the right,
// full width on a phone. One at a time; Escape or the close button dismisses it.
// Forms and workflows that need room belong in a Dialog instead.
export function Inspector({
  title,
  subtitle,
  inline,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  // True when the screen is wide enough to show it beside the conversation.
  inline: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const opener = useRef<HTMLElement | null>(null);

  const header = (
    <header className="flex shrink-0 items-center gap-2 border-b border-line px-4 pb-2 pt-[max(0.5rem,env(safe-area-inset-top))]">
      <div className="min-w-0 flex-1">
        <RadixTitle inline={inline}>{title}</RadixTitle>
        {subtitle ? <p className="truncate text-xs text-muted">{subtitle}</p> : null}
      </div>
      {/* A plain button, not IconButton: it takes focus when the sheet opens, and a tooltip on it
          would swallow the first Escape. */}
      {inline ? (
        <button type="button" aria-label={t("common.close")} title={t("common.close")} onClick={onClose} className={CLOSE}>
          <X size={18} aria-hidden="true" />
        </button>
      ) : (
        <RadixDialog.Close aria-label={t("common.close")} title={t("common.close")} className={CLOSE}>
          <X size={18} aria-hidden="true" />
        </RadixDialog.Close>
      )}
    </header>
  );

  if (inline) {
    return (
      <aside
        aria-label={title}
        onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}
        className="flex w-[26rem] shrink-0 flex-col border-l border-line bg-surface"
      >
        {header}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      </aside>
    );
  }

  return (
    <RadixDialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 animate-fade-in bg-scrim" />
        <RadixDialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={() => { opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
          onCloseAutoFocus={(event) => { event.preventDefault(); opener.current?.focus(); }}
          className="fixed inset-y-0 right-0 z-50 flex w-[min(28rem,100vw)] animate-inspector-in flex-col border-l border-line bg-surface pb-[env(safe-area-inset-bottom)] shadow-xl"
        >
          {header}
          <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

// Radix needs its own Title inside the overlay version for the dialog's accessible name.
function RadixTitle({ inline, children }: { inline: boolean; children: ReactNode }) {
  const cls = "truncate text-[15px] font-semibold";
  return inline ? <h2 className={cls}>{children}</h2> : <RadixDialog.Title className={cls}>{children}</RadixDialog.Title>;
}
