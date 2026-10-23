"use client";

import { ArrowUp, Paperclip, Sparkles, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { MAX_SKILLS_PER_MESSAGE, skillCompletions, type SkillSummary } from "@/lib/skills";
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
  focusRequested = false,
  onFocused,
  skills,
  chosenSkills = [],
  onToggleSkill,
  onSaveAsSkill,
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
  // New chat asks for the box to take focus; onFocused clears the request.
  focusRequested?: boolean;
  onFocused?: () => void;
  // The agent's skills. With onToggleSkill the box offers a picker and `/name` completion;
  // chosen ones are sent with the next message only. A skill never starts on its own.
  skills?: SkillSummary[];
  chosenSkills?: string[];
  onToggleSkill?: (name: string) => void;
  // Opens the skill editor seeded from this conversation. Sends and saves nothing.
  onSaveAsSkill?: () => void;
}) {
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const enabledSkills = (skills ?? []).filter((s) => s.enabled);
  const completions = onToggleSkill ? skillCompletions(value, enabledSkills, chosenSkills) : [];
  const pick = (name: string) => {
    onToggleSkill?.(name);
    if (value.startsWith("/")) onChange("");
    box.current?.focus();
  };

  useEffect(() => {
    if (!focusRequested) return;
    box.current?.focus();
    onFocused?.();
  }, [focusRequested, onFocused]);

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
          {chosenSkills.length > 0 && onToggleSkill && (
            <ul aria-label="Skills for this message" className="flex flex-wrap gap-1.5 px-2 pt-1">
              {chosenSkills.map((name) => (
                <li key={name} className="inline-flex max-w-full items-center gap-1 rounded-full border border-accent/40 bg-accent/10 py-0.5 pl-2.5 pr-1 text-xs text-ink">
                  <Sparkles size={12} aria-hidden="true" className="shrink-0 text-accent" />
                  <span className="truncate font-mono">{name}</span>
                  <button type="button" onClick={() => onToggleSkill(name)} aria-label={`Remove skill ${name}`} className="inline-flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted hover:bg-hover hover:text-ink">
                    <X size={12} aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {completions.length > 0 && (
            <ul role="listbox" aria-label="Skills" className="mx-2 mt-1 max-h-48 overflow-y-auto rounded-field border border-line bg-raised text-sm">
              {completions.map((s, i) => (
                <li key={s.name} role="option" aria-selected={i === 0}>
                  <button type="button" onClick={() => pick(s.name)} className={cn("flex w-full min-w-0 cursor-pointer flex-col px-3 py-1.5 text-left hover:bg-hover", i === 0 && "bg-hover")}>
                    <span className="font-mono text-xs">/{s.name}</span>
                    <span className="truncate text-xs text-muted">{s.description}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {pickerOpen && onToggleSkill && (
            <div role="group" aria-label="Pick skills" className="mx-2 mt-1 max-h-48 overflow-y-auto rounded-field border border-line bg-raised text-sm">
              {enabledSkills.length === 0 ? (
                <p className="px-3 py-2 text-xs text-muted">No skills are switched on for this agent. Add one in the agent&apos;s settings.</p>
              ) : (
                enabledSkills.map((s) => {
                  const on = chosenSkills.includes(s.name);
                  const full = !on && chosenSkills.length >= MAX_SKILLS_PER_MESSAGE;
                  return (
                    <label key={s.name} className={cn("flex min-w-0 items-start gap-2 px-3 py-1.5", full ? "opacity-50" : "cursor-pointer hover:bg-hover")}>
                      <input type="checkbox" className="mt-1" checked={on} disabled={full} onChange={() => onToggleSkill(s.name)} />
                      <span className="min-w-0">
                        <span className="block font-mono text-xs">/{s.name}</span>
                        <span className="block truncate text-xs text-muted">{s.description}</span>
                      </span>
                    </label>
                  );
                })
              )}
              {enabledSkills.length > 0 && <p className="px-3 py-1 text-xs text-faint">Up to {MAX_SKILLS_PER_MESSAGE} per message, used for the next message only.</p>}
            </div>
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
            {onToggleSkill && (
              <button
                type="button"
                onClick={() => setPickerOpen((open) => !open)}
                disabled={sending}
                aria-label="Pick skills"
                aria-expanded={pickerOpen}
                title="Use a saved skill for this message (or type / and a name)"
                className={cn("inline-flex size-10 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted transition-colors duration-(--dur-fast) hover:bg-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-50 touch:size-11", (pickerOpen || chosenSkills.length > 0) && "text-accent")}
              >
                <Sparkles size={18} aria-hidden="true" />
              </button>
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
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && completions.length > 0) {
                  e.preventDefault();
                  pick(completions[0].name);
                  return;
                }
                if (e.key === "Escape" && pickerOpen) setPickerOpen(false);
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
        {onSaveAsSkill && (
          <div className="mt-1 flex justify-end px-3">
            <button type="button" onClick={onSaveAsSkill} className="cursor-pointer text-xs text-muted underline-offset-2 hover:text-ink hover:underline">
              Save as skill
            </button>
          </div>
        )}
        {blockedReason && value.trim() !== "" && <p className="mt-1.5 px-3 text-xs text-muted">{blockedReason}</p>}
      </div>
    </div>
  );
}
