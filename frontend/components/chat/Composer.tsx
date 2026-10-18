"use client";

import { useEffect, useRef, useState } from "react";
import { PendingAttachments } from "./Attachments";

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
      className={`border-t bg-background px-4 py-3 ${
        dragging ? "border-sky-400 bg-sky-50 dark:bg-sky-950/30" : "border-zinc-200 dark:border-zinc-800"
      }`}
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
        {error && <p role="alert" className="mb-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
        {attachNotice && <p role="status" className="mb-2 text-sm text-amber-700 dark:text-amber-400">{attachNotice}</p>}
        {onRemoveFile && <PendingAttachments files={files} onRemove={onRemoveFile} disabled={sending} />}
        {dragging && <p className="mb-2 text-xs text-sky-700 dark:text-sky-300">Drop to attach</p>}
        <div className="flex items-end gap-2">
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
                className="h-10 cursor-pointer rounded-md border border-zinc-300 px-3 text-sm hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
              >
                📎
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
            className="min-h-10 flex-1 resize-none rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm placeholder:text-zinc-500 dark:border-zinc-700"
          />
          {running ? (
            <button
              onClick={onStop}
              disabled={stopping}
              className="h-10 cursor-pointer rounded-md border border-zinc-400 px-4 text-sm font-medium transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-600 dark:hover:bg-zinc-900"
            >
              {stopping ? "Stopping…" : "Stop"}
            </button>
          ) : (
            <button
              onClick={onSend}
              disabled={!canSend}
              className="h-10 cursor-pointer rounded-md bg-zinc-900 px-4 text-sm font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              {sending ? "Sending…" : "Send"}
            </button>
          )}
        </div>
        {blockedReason && value.trim() !== "" && (
          <p className="mt-1 text-xs text-zinc-500">{blockedReason}</p>
        )}
      </div>
    </div>
  );
}
