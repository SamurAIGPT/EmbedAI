"use client";

import { useEffect, useRef } from "react";

const MAX_HEIGHT_PX = 200;

// The message box. Enter sends, Shift+Enter adds a line. While it cannot send it
// says why, instead of just going grey.
export function Composer({
  value,
  onChange,
  onSend,
  sending,
  blockedReason,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  // Why a message cannot be sent right now (a turn is running, the session is
  // broken, ...). Null when it can.
  blockedReason: string | null;
  error: string | null;
}) {
  const box = useRef<HTMLTextAreaElement>(null);

  // Grows with the text up to a limit.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  const canSend = !sending && blockedReason === null && value.trim() !== "";

  return (
    <div className="border-t border-zinc-200 bg-background px-4 py-3 dark:border-zinc-800">
      <div className="mx-auto max-w-3xl">
        {error && <p className="mb-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="flex items-end gap-2">
          <textarea
            ref={box}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (canSend) onSend();
              }
            }}
            rows={1}
            placeholder={blockedReason ?? "Message Claude"}
            aria-label="Message"
            className="min-h-10 flex-1 resize-none rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm placeholder:text-zinc-500 dark:border-zinc-700"
          />
          <button
            onClick={onSend}
            disabled={!canSend}
            className="h-10 cursor-pointer rounded-md bg-zinc-900 px-4 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
        {blockedReason && value.trim() !== "" && (
          <p className="mt-1 text-xs text-zinc-500">{blockedReason}</p>
        )}
      </div>
    </div>
  );
}
