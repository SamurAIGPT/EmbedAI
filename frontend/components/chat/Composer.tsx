"use client";

import { ArrowUp, Paperclip, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { PendingAttachments } from "./Attachments";

const MAX_HEIGHT_PX = 200;

// The message box: one rounded field with the paperclip on the left and Send (or Stop while an
// answer is being written) on the right. Enter sends, Shift+Enter adds a line, and Enter during
// IME composition does neither. While it cannot send it says why, instead of just going grey.
export function Composer({
  value,
  onChange,
  onSend,
  sending,
  blockedReason,
  error,
  running,
  stopping,
  onStop,
  files = [],
  onAddFiles,
  onRemoveFile,
  attachNotice = null,
}: {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  // Why a message cannot be sent right now (a turn is running, the session is
  // broken, ...). Null when it can.
  blockedReason: string | null;
  error: string | null;
  // An answer is being written: the button becomes Stop.
  running: boolean;
  // Stop was pressed and the answer has not ended yet.
  stopping: boolean;
  onStop: () => void;
  // Files chosen for the next message (docs/EVERYDAY.md). Without onAddFiles
  // the composer offers no attachments.
  files?: File[];
  onAddFiles?: (files: File[]) => void;
  onRemoveFile?: (index: number) => void;
  // Why an attachment was refused or cannot be sent, if anything.
  attachNotice?: string | null;
}) {
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  // Grows with the text up to a limit.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [value]);

  const canSend = !sending && blockedReason === null && value.trim() !== "";
  const carriesFiles = (types: readonly string[]) => types.includes("Files");

  return (
    <div
      className="shrink-0 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2 sm:px-6"
      onDragOver={(e) => {
        if (!onAddFiles || !carriesFiles(Array.from(e.dataTransfer.types))) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        if (!onAddFiles || !carriesFiles(Array.from(e.dataTransfer.types))) return;
        e.preventDefault();
        setDragging(false);
        onAddFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <div className="mx-auto max-w-3xl">
        {error && <p role="alert" className="mb-2 text-sm text-danger">{error}</p>}
        {attachNotice && <p role="status" className="mb-2 text-sm text-warning">{attachNotice}</p>}
        <div
          className={cn(
            "rounded-3xl border bg-raised px-2 py-2 shadow-sm transition-colors duration-(--dur-fast) focus-within:border-accent/60",
            dragging ? "border-accent bg-accent/10" : "border-line",
          )}
        >
          {onRemoveFile && files.length > 0 && (
            <div className="px-2 pt-1"><PendingAttachments files={files} onRemove={onRemoveFile} disabled={sending} /></div>
          )}
          {dragging && <p className="px-3 pb-1 text-xs text-accent">Drop to attach</p>}
          <div className="flex items-end gap-1">
            {onAddFiles && (
              <>
                <input
                  ref={picker}
                  type="file"
                  multiple
                  hidden
                  aria-hidden
                  tabIndex={-1}
                  accept="image/png,image/jpeg,image/gif,image/webp,.txt,.md,.csv,.tsv,.json,.log,.yaml,.yml,.xml,.html,.css,.js,.ts,.tsx,.py,.sh,.sql,.toml,.ini"
                  onChange={(e) => {
                    onAddFiles(Array.from(e.target.files ?? []));
                    e.target.value = "";
                  }}
                />
                <button
                  type="button"
                  onClick={() => picker.current?.click()}
                  disabled={sending}
                  aria-label="Attach files"
                  title="Attach images or text files (or paste, or drop them here)"
                  className="inline-flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted transition-colors duration-(--dur-fast) hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 touch:size-11"
                >
                  <Paperclip size={18} aria-hidden="true" />
                </button>
              </>
            )}
            <textarea
              ref={box}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              onPaste={(e) => {
                const pasted = Array.from(e.clipboardData.files);
                if (!onAddFiles || pasted.length === 0) return;
                e.preventDefault();
                onAddFiles(pasted);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  if (canSend) onSend();
                }
              }}
              rows={1}
              placeholder={blockedReason ?? "Message your agent"}
              aria-label="Message"
              className="max-h-52 min-h-10 min-w-0 flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] leading-5 outline-none placeholder:text-faint focus-visible:outline-none"
            />
            {running ? (
              <button
                type="button"
                onClick={onStop}
                disabled={stopping}
                className="inline-flex h-10 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border border-line bg-raised px-4 text-sm font-medium text-ink transition-colors duration-(--dur-fast) hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50 touch:h-11"
              >
                <Square size={13} aria-hidden="true" />
                {stopping ? "Stopping…" : "Stop"}
              </button>
            ) : (
              <button
                type="button"
                onClick={onSend}
                disabled={!canSend}
                className="inline-flex h-10 shrink-0 cursor-pointer items-center gap-1.5 rounded-full bg-accent px-4 text-sm font-medium text-accent-fg transition-opacity duration-(--dur-fast) hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40 touch:h-11"
              >
                <ArrowUp size={16} aria-hidden="true" />
                {sending ? "Sending…" : "Send"}
              </button>
            )}
          </div>
        </div>
        {blockedReason && value.trim() !== "" && <p className="mt-1.5 px-3 text-xs text-muted">{blockedReason}</p>}
      </div>
    </div>
  );
}
