"use client";

import { useState } from "react";
import { canAlwaysAllow } from "@/lib/approvals";
import { isComputerTool, toolLabel } from "@/lib/computers";
import { failureNextStep, turnUsageText } from "@/lib/everyday";
import { compactedLabel, recalledLabel } from "@/lib/memory";
import type { TranscriptItem } from "@/lib/transcript";
import { Markdown } from "./Markdown";
import { MessageAttachments } from "./Attachments";

type Item<K extends TranscriptItem["kind"]> = Extract<TranscriptItem, { kind: K }>;

// A value is shown exactly as it will be used, so a person approves what runs:
// strings verbatim (a command, file contents), anything else as JSON.
function formatValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

const PREVIEW_CHARS = 4000;

// Long tool output is cut for display so one big result cannot swamp the page.
function LongText({ value }: { value: string }) {
  const [all, setAll] = useState(false);
  const cut = !all && value.length > PREVIEW_CHARS;
  return (
    <>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-zinc-100 p-2 font-mono text-xs text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
        {cut ? value.slice(0, PREVIEW_CHARS) : value}
      </pre>
      {cut && (
        <button
          onClick={() => setAll(true)}
          className="mt-1 cursor-pointer text-xs text-zinc-500 underline"
        >
          Show all ({value.length.toLocaleString()} characters)
        </button>
      )}
    </>
  );
}

export function UserMessage({ item, threadId = null }: { item: Item<"user">; threadId?: number | null }) {
  return (
    <div className="flex flex-col items-end gap-2">
      <MessageAttachments threadId={threadId} items={item.attachments ?? []} />
      <p className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-zinc-900 px-4 py-2 text-sm text-white dark:bg-zinc-100 dark:text-zinc-900">
        {item.text}
      </p>
    </div>
  );
}

export function AssistantMessage({
  text,
  incomplete = false,
  live = false,
}: {
  text: string;
  incomplete?: boolean;
  live?: boolean;
}) {
  return (
    <div className="max-w-[85%] text-sm">
      <Markdown text={text} />
      {live && <span className="animate-pulse">▍</span>}
      {incomplete && (
        <p className="mt-1 text-xs text-zinc-500">
          This message stopped before it was finished.
        </p>
      )}
    </div>
  );
}

const STEP_MARK = {
  running: { mark: "…", color: "text-amber-600 dark:text-amber-400" },
  done: { mark: "✓", color: "text-emerald-600 dark:text-emerald-400" },
  error: { mark: "✗", color: "text-red-600 dark:text-red-400" },
  // Never finished: the answer was stopped, failed or was interrupted first.
  stopped: { mark: "■", color: "text-zinc-400" },
} as const;

export function ToolStep({ item }: { item: Item<"tool"> }) {
  const { mark, color } = STEP_MARK[item.state];
  const input = formatValue(item.input);
  return (
    <details className="group rounded-md border border-zinc-200 text-xs dark:border-zinc-800">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
        <span className={color}>{mark}</span>
        {isComputerTool(item.name) && <ComputerBadge />}
        <span className="font-mono text-zinc-700 dark:text-zinc-300">{item.name}</span>
        <span className="min-w-0 flex-1 truncate text-zinc-500">{item.summary}</span>
        <span className="text-zinc-400 group-open:rotate-90">›</span>
      </summary>
      <div className="space-y-2 border-t border-zinc-200 p-3 dark:border-zinc-800">
        <div>
          <p className="mb-1 text-zinc-500">Input</p>
          <LongText value={input} />
        </div>
        {item.state === "stopped" && (
          <p className="text-zinc-500">This call did not finish: the answer ended first.</p>
        )}
        {(item.state === "done" || item.state === "error") && (
          <div>
            <p className="mb-1 text-zinc-500">
              {item.state === "error" ? "Error" : "Output"}
              {item.truncated && " (cut at the stored limit)"}
            </p>
            {item.output ? (
              <LongText value={item.output} />
            ) : (
              <p className="text-zinc-500">No output.</p>
            )}
          </div>
        )}
      </div>
    </details>
  );
}

const APPROVAL_LABEL: Record<Item<"approval">["status"], string> = {
  pending: "Waiting for you",
  allowed: "Allowed",
  denied: "Denied",
  expired: "Expired: nobody answered in time, so it did not run",
  cancelled: "Cancelled: the run ended before it was answered",
};

// What became of "Always allow this command" on this card: the saved rule
// (null after Undo), or why it could not be saved.
export type Remembered = { ruleId: number | null; error: string | null };

export function ApprovalCard({
  item,
  answering,
  error,
  onAnswer,
  remembered,
  undoneRules = [],
  onUndo,
}: {
  item: Item<"approval">;
  answering: boolean;
  error: string | null;
  onAnswer: (decision: "allow" | "deny", remember?: boolean) => void;
  remembered?: Remembered;
  // Rules removed since the page opened: their cards stop offering Undo.
  undoneRules?: number[];
  // Stop always allowing: removes the saved rule; the next such command asks.
  onUndo?: (ruleId: number) => void;
}) {
  const pending = item.status === "pending";
  const rememberable = canAlwaysAllow(item.tool, item.input);
  const byRule = item.resolvedBy === "saved_rule";
  const savedRuleId = remembered?.ruleId ?? (byRule ? item.ruleId : null);
  const undone = savedRuleId != null && undoneRules.includes(savedRuleId);
  return (
    <div
      className={`space-y-2 rounded-md border p-3 text-sm ${
        pending
          ? "border-amber-400 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30"
          : "border-zinc-200 dark:border-zinc-800"
      }`}
    >
      <p
        className={`text-xs font-medium ${
          pending ? "text-amber-800 dark:text-amber-300" : "text-zinc-500"
        }`}
      >
        {isComputerTool(item.tool) && <ComputerBadge />}
        {pending ? (
          isComputerTool(item.tool) ? (
            <>
              Your agent wants to {toolLabel(item.tool)}, an isolated machine, not this one. It will not run
              until you allow it.
            </>
          ) : (
            <>
              Your agent wants to use <span className="font-mono">{item.tool}</span>. It will not run
              until you allow it.
            </>
          )
        ) : (
          <>
            <span className="font-mono">{item.tool}</span>:{" "}
            {byRule && item.status === "allowed"
              ? "Allowed by a saved rule, without asking"
              : item.resolvedBy === "computer_read"
                ? "Allowed without asking: reading inside the agent's computer"
                : item.resolvedBy === "accept_edits"
                  ? "Allowed without asking: edits are accepted"
                  : item.resolvedBy === "plan_mode"
                    ? "Refused: plan only changes nothing"
                    : APPROVAL_LABEL[item.status]}
            {!pending && item.summary && <span className="font-mono text-zinc-400"> · {item.summary}</span>}
          </>
        )}
      </p>
      {pending && (
        <dl className="space-y-1">
          {Object.entries(item.input).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs text-zinc-500">{key}</dt>
              <dd>
                <LongText value={formatValue(value)} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {pending && (
        <div className="flex gap-2">
          <button
            onClick={() => onAnswer("allow")}
            disabled={answering}
            className="cursor-pointer rounded-md bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            Allow
          </button>
          {rememberable && (
            <button
              onClick={() => onAnswer("allow", true)}
              disabled={answering}
              title="Allow it now, and run exactly this command in this folder without asking from now on"
              className="cursor-pointer rounded-md border border-zinc-300 px-4 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
            >
              Always allow this command
            </button>
          )}
          <button
            onClick={() => onAnswer("deny")}
            disabled={answering}
            className="cursor-pointer rounded-md border border-zinc-300 px-4 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
          >
            Deny
          </button>
        </div>
      )}
      {!pending && remembered?.error && (
        <p className="text-xs text-zinc-500">Allowed this once only: {remembered.error}.</p>
      )}
      {!pending && undone && (
        <p className="text-xs text-zinc-500">No longer always allowed. Next time it asks again.</p>
      )}
      {!pending && savedRuleId != null && !undone && onUndo && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
          {remembered?.ruleId != null ? "Always allowed in this folder from now on." : "A saved rule allows this command."}
          <button
            onClick={() => onUndo(savedRuleId)}
            className="cursor-pointer underline hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            {remembered?.ruleId != null ? "Undo" : "Stop always allowing"}
          </button>
        </p>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}

const END_TEXT: Record<Item<"turn_end">["status"], (item: Item<"turn_end">) => string | null> = {
  completed: () => null,
  failed: (i) => i.error || "The run failed.",
  cancelled: () => "You stopped this answer.",
  interrupted: (i) =>
    i.reason === "shutdown"
      ? "The app was closed while the agent was working. This message was not resumed or sent again."
      : "The app stopped while the agent was working. This message was not resumed or sent again.",
};

export function TurnEnd({ item }: { item: Item<"turn_end"> }) {
  const message = END_TEXT[item.status](item);
  const usage = item.status === "completed" || item.usage ? turnUsageText(item.usage) : null;
  const next = item.status === "failed" ? failureNextStep(item.errorCode) : null;
  const bad = item.status === "failed";
  return (
    <>
      {message && (
        <div
          role={bad ? "alert" : undefined}
          className={`rounded-md border px-3 py-2 text-xs ${
            bad
              ? "border-red-300 text-red-700 dark:border-red-900 dark:text-red-400"
              : "border-zinc-200 text-zinc-500 dark:border-zinc-800"
          }`}
        >
          <p className="whitespace-pre-wrap break-words">{message}</p>
          {next && <p className="mt-1 text-zinc-600 dark:text-zinc-400">{next}</p>}
        </div>
      )}
      {usage && (
        <p className="text-right text-[11px] text-zinc-400 dark:text-zinc-500" title="As reported by the engine">
          {usage}
        </p>
      )}
    </>
  );
}

export function SessionReset({ item }: { item: Item<"session_reset"> }) {
  return (
    <p className="border-y border-dashed border-zinc-300 py-2 text-center text-xs text-zinc-500 dark:border-zinc-700">
      {item.message ||
        "The engine's session for this conversation was reset. The messages above are shown for reference only."}
    </p>
  );
}

// Marks a call that runs in the agent's computer rather than on this machine.
function ComputerBadge() {
  return (
    <span
      className="mr-1 rounded bg-sky-100 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-sky-800 uppercase dark:bg-sky-950 dark:text-sky-300"
      title="Runs in the agent's computer (an isolated container), not on this machine"
    >
      computer
    </span>
  );
}

// What recall put in front of the person's message. Always shown, so nothing
// the engine was given is hidden (docs/MEMORY.md).
export function RecalledChip({ item }: { item: Item<"recalled"> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="self-end text-xs text-zinc-500">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="rounded-full border border-zinc-300 px-2 py-0.5 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
      >
        🧠 {recalledLabel(item.notes.length, item.conversations.length)}
      </button>
      {open && (
        <ul className="mt-1 max-w-xl space-y-1 rounded-md border border-zinc-200 p-2 text-left dark:border-zinc-800">
          <li className="text-zinc-500">Background for the agent, not instructions. It grants no permission.</li>
          {item.notes.map((n, i) => (
            <li key={`n${i}`}>
              <span className="font-mono">memory/{n.name}</span>: {n.snippet}
            </li>
          ))}
          {item.conversations.map((c, i) => (
            <li key={`c${i}`}>
              <span className="font-medium">“{c.title || "untitled"}”</span>
              {c.at ? ` (${c.at.slice(0, 10)})` : ""}, {c.role === "user" ? "you said" : "the agent said"}: {c.snippet}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Earlier messages were folded into a summary for the engine.
export function CompactedMarker({ item }: { item: Item<"compacted"> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-y border-dashed border-zinc-300 py-2 text-center text-xs text-zinc-500 dark:border-zinc-700">
      <p>{compactedLabel(item.folded)}</p>
      {item.summary && (
        <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="mt-1 underline">
          {open ? "Hide the summary" : "Show the summary"}
        </button>
      )}
      {open && <pre className="mt-2 whitespace-pre-wrap break-words text-left font-mono">{item.summary}</pre>}
    </div>
  );
}
