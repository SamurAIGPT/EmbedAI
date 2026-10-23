"use client";

import { Check, ChevronRight, Loader2, Square, X } from "lucide-react";
import { useState } from "react";
import { canAlwaysAllow, isScreenInput } from "@/lib/approvals";
import { isComputerTool, toolLabel } from "@/lib/computers";
import { cn } from "@/lib/cn";
import { failureNextStep, interruptedText, turnUsageText } from "@/lib/everyday";
import { compactedLabel, recalledLabel } from "@/lib/memory";
import type { TranscriptItem } from "@/lib/transcript";
import { toolRunSummary } from "@/lib/transcript-view";
import { Button } from "@/components/ui/Button";
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
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-field bg-code p-2.5 font-mono text-xs text-ink">
        {cut ? value.slice(0, PREVIEW_CHARS) : value}
      </pre>
      {cut && (
        <button onClick={() => setAll(true)} className="mt-1 cursor-pointer text-xs text-muted underline">
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
      <p className="max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-bubble px-4 py-2.5 text-[15px] leading-relaxed text-ink sm:max-w-[80%]">
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
    <div className="max-w-full text-[15px] leading-relaxed">
      <Markdown text={text} />
      {live && <span aria-hidden="true" className="animate-pulse text-muted">▍</span>}
      {incomplete && <p className="mt-1 text-xs text-muted">This message stopped before it was finished.</p>}
    </div>
  );
}

const STEP_ICON = {
  running: { icon: <Loader2 size={13} className="animate-spin" />, color: "text-warning", label: "Running" },
  done: { icon: <Check size={13} />, color: "text-success", label: "Done" },
  error: { icon: <X size={13} />, color: "text-danger", label: "Failed" },
  // Never finished: the answer was stopped, failed or was interrupted first.
  stopped: { icon: <Square size={11} />, color: "text-faint", label: "Stopped" },
} as const;

export function ToolStep({ item }: { item: Item<"tool"> }) {
  const step = STEP_ICON[item.state];
  const input = formatValue(item.input);
  return (
    <details className="group rounded-field border border-line bg-raised/60 text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 touch:py-3 [&::-webkit-details-marker]:hidden">
        <span className={cn("inline-flex w-4 shrink-0 justify-center", step.color)} role="img" aria-label={step.label}>
          {step.icon}
        </span>
        {isComputerTool(item.name) && <ComputerBadge />}
        <span className="font-mono text-ink">{item.name}</span>
        <span className="min-w-0 flex-1 truncate text-muted">{item.summary}</span>
        <ChevronRight size={14} aria-hidden="true" className="shrink-0 text-faint transition-transform group-open:rotate-90" />
      </summary>
      <div className="space-y-2 border-t border-line p-3">
        <div>
          <p className="mb-1 text-muted">Input</p>
          <LongText value={input} />
        </div>
        {item.state === "stopped" && <p className="text-muted">This call did not finish: the answer ended first.</p>}
        {(item.state === "done" || item.state === "error") && (
          <div>
            <p className="mb-1 text-muted">
              {item.state === "error" ? "Error" : "Output"}
              {item.truncated && " (cut at the stored limit)"}
            </p>
            {item.output ? <LongText value={item.output} /> : <p className="text-muted">No output.</p>}
          </div>
        )}
      </div>
    </details>
  );
}

// Three or more tool calls in a row, folded into one line while they are done.
export function ToolRun({ tools }: { tools: Item<"tool">[] }) {
  const { total, running, failed } = toolRunSummary(tools);
  const summary = running > 0 ? `Working · ${total} tool calls so far` : `Used ${total} tools`;
  return (
    <details open={running > 0 || failed > 0} className="group rounded-card border border-line bg-raised/40">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs text-muted touch:py-3 [&::-webkit-details-marker]:hidden">
        {running > 0 ? <Loader2 size={13} aria-hidden="true" className="animate-spin text-warning" /> : <Check size={13} aria-hidden="true" className="text-success" />}
        <span className="flex-1 font-medium text-ink">{summary}</span>
        {failed > 0 && <span className="text-danger">{failed} failed</span>}
        <ChevronRight size={14} aria-hidden="true" className="shrink-0 text-faint transition-transform group-open:rotate-90" />
      </summary>
      <div className="space-y-1.5 border-t border-line p-2">
        {tools.map((tool) => (
          <ToolStep key={tool.key} item={tool} />
        ))}
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
  const screenInput = isScreenInput(item.tool);
  const byRule = item.resolvedBy === "saved_rule";
  const savedRuleId = remembered?.ruleId ?? (byRule ? item.ruleId : null);
  const undone = savedRuleId != null && undoneRules.includes(savedRuleId);
  return (
    <div
      className={cn(
        "space-y-2.5 rounded-card border p-3.5 text-sm",
        pending ? "border-warning/60 bg-warning/10" : "border-line bg-raised/40",
      )}
    >
      <p className={cn("text-xs font-medium", pending ? "text-ink" : "text-muted")}>
        {isComputerTool(item.tool) && <ComputerBadge />}
        {pending ? (
          isComputerTool(item.tool) ? (
            <>
              {screenInput ? "Let your agent use this computer’s screen, keyboard and browser for this turn." : <>Your agent wants to {toolLabel(item.tool)}, an isolated machine, not this one.</>} It will not run
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
            {!pending && item.summary && <span className="font-mono text-faint"> · {item.summary}</span>}
          </>
        )}
      </p>
      {pending && (
        <dl className="space-y-1.5">
          {Object.entries(item.input).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs text-muted">{key}</dt>
              <dd>
                <LongText value={formatValue(value)} />
              </dd>
            </div>
          ))}
        </dl>
      )}
      {pending && (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => onAnswer("allow")} disabled={answering}>
            {screenInput ? "Allow this turn" : "Allow"}
          </Button>
          {rememberable && (
            <Button
              onClick={() => onAnswer("allow", true)}
              disabled={answering}
              title="Allow it now, and run exactly this command in this folder without asking from now on"
            >
              Always allow this command
            </Button>
          )}
          <Button onClick={() => onAnswer("deny")} disabled={answering}>
            Deny
          </Button>
        </div>
      )}
      {!pending && remembered?.error && <p className="text-xs text-muted">Allowed this once only: {remembered.error}.</p>}
      {!pending && undone && <p className="text-xs text-muted">No longer always allowed. Next time it asks again.</p>}
      {!pending && savedRuleId != null && !undone && onUndo && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {remembered?.ruleId != null ? "Always allowed in this folder from now on." : "A saved rule allows this command."}
          <button onClick={() => onUndo(savedRuleId)} className="cursor-pointer underline hover:text-ink">
            {remembered?.ruleId != null ? "Undo" : "Stop always allowing"}
          </button>
        </p>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}

const END_TEXT: Record<Item<"turn_end">["status"], (item: Item<"turn_end">) => string | null> = {
  completed: () => null,
  failed: (i) => i.error || "The run failed.",
  cancelled: () => "You stopped this answer.",
  interrupted: (i) => interruptedText(i.reason, i.replay),
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
          className={cn("rounded-card border px-3.5 py-2.5 text-sm", bad ? "border-danger/50 bg-danger/10 text-ink" : "border-line text-muted")}
        >
          <p className={cn("whitespace-pre-wrap break-words", bad && "text-danger")}>{message}</p>
          {next && <p className="mt-1 text-xs text-muted">{next}</p>}
        </div>
      )}
      {usage && (
        <p className="text-right text-[11px] text-faint" title="As reported by the engine">
          {usage}
        </p>
      )}
    </>
  );
}

export function SessionReset({ item }: { item: Item<"session_reset"> }) {
  return (
    <p className="border-y border-dashed border-line py-2 text-center text-xs text-muted">
      {item.message ||
        "The engine's session for this conversation was reset. The messages above are shown for reference only."}
    </p>
  );
}

// Marks a call that runs in the agent's computer rather than on this machine.
function ComputerBadge() {
  return (
    <span
      className="mr-1 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-accent"
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
    <div className="self-end text-xs text-muted">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="cursor-pointer rounded-full border border-line px-2.5 py-0.5 hover:bg-hover"
      >
        🧠 {recalledLabel(item.notes.length, item.conversations.length)}
      </button>
      {open && (
        <ul className="mt-1 max-w-xl space-y-1 rounded-field border border-line bg-raised p-2 text-left">
          <li className="text-muted">Background for the agent, not instructions. It grants no permission.</li>
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

// The turn moved to the agent's fallback engine: always shown, never silent.
export function EngineSwitched({ item }: { item: Item<"engine_switched"> }) {
  return (
    <div role="status" className="rounded-card border border-warning/50 bg-warning/10 px-3.5 py-2.5 text-xs">
      <p className="font-medium text-ink">
        Switched engine: {item.from} → {item.to}
        {item.model ? ` (${item.model})` : ""}
      </p>
      {item.message && <p className="mt-1 whitespace-pre-wrap break-words text-muted">{item.message}</p>}
    </div>
  );
}

// Earlier messages were folded into a summary for the engine.
export function CompactedMarker({ item }: { item: Item<"compacted"> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-y border-dashed border-line py-2 text-center text-xs text-muted">
      <p>{compactedLabel(item.folded, item.writtenBy)}</p>
      {item.summary && (
        <button onClick={() => setOpen((v) => !v)} aria-expanded={open} className="mt-1 cursor-pointer underline">
          {open ? "Hide the summary" : "Show the summary"}
        </button>
      )}
      {open && <pre className="mt-2 whitespace-pre-wrap break-words text-left font-mono">{item.summary}</pre>}
    </div>
  );
}
