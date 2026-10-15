"use client";

import { useState } from "react";
import type { TranscriptItem } from "@/lib/transcript";

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

export function UserMessage({ item }: { item: Item<"user"> }) {
  return (
    <div className="flex justify-end">
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
      <p className="whitespace-pre-wrap break-words text-zinc-800 dark:text-zinc-200">
        {text}
        {live && <span className="animate-pulse">▍</span>}
      </p>
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
} as const;

export function ToolStep({ item }: { item: Item<"tool"> }) {
  const { mark, color } = STEP_MARK[item.state];
  const input = formatValue(item.input);
  return (
    <details className="group rounded-md border border-zinc-200 text-xs dark:border-zinc-800">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
        <span className={color}>{mark}</span>
        <span className="font-mono text-zinc-700 dark:text-zinc-300">{item.name}</span>
        <span className="min-w-0 flex-1 truncate text-zinc-500">{item.summary}</span>
        <span className="text-zinc-400 group-open:rotate-90">›</span>
      </summary>
      <div className="space-y-2 border-t border-zinc-200 p-3 dark:border-zinc-800">
        <div>
          <p className="mb-1 text-zinc-500">Input</p>
          <LongText value={input} />
        </div>
        {item.state !== "running" && (
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

export function ApprovalCard({
  item,
  answering,
  error,
  onAnswer,
}: {
  item: Item<"approval">;
  answering: boolean;
  error: string | null;
  onAnswer: (decision: "allow" | "deny") => void;
}) {
  const pending = item.status === "pending";
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
        {pending ? (
          <>
            Claude wants to use <span className="font-mono">{item.tool}</span>. It will not run
            until you allow it.
          </>
        ) : (
          <>
            <span className="font-mono">{item.tool}</span>: {APPROVAL_LABEL[item.status]}
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
          <button
            onClick={() => onAnswer("deny")}
            disabled={answering}
            className="cursor-pointer rounded-md border border-zinc-300 px-4 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900"
          >
            Deny
          </button>
        </div>
      )}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}

const END_TEXT: Record<Item<"turn_end">["status"], (item: Item<"turn_end">) => string | null> = {
  completed: () => null,
  failed: (i) => i.error || "The run failed.",
  cancelled: () => "The run was stopped.",
  interrupted: (i) =>
    i.reason === "shutdown"
      ? "The app was closed while Claude was working. This message was not resumed or sent again."
      : "The app stopped while Claude was working. This message was not resumed or sent again.",
};

export function TurnEnd({ item }: { item: Item<"turn_end"> }) {
  const message = END_TEXT[item.status](item);
  if (!message) return null;
  const bad = item.status === "failed";
  return (
    <p
      className={`rounded-md border px-3 py-2 text-xs ${
        bad
          ? "border-red-300 text-red-700 dark:border-red-900 dark:text-red-400"
          : "border-zinc-200 text-zinc-500 dark:border-zinc-800"
      }`}
    >
      {message}
    </p>
  );
}

export function SessionReset({ item }: { item: Item<"session_reset"> }) {
  return (
    <p className="border-y border-dashed border-zinc-300 py-2 text-center text-xs text-zinc-500 dark:border-zinc-700">
      {item.message ||
        "Claude's memory of this conversation was reset. The messages above are shown for reference only."}
    </p>
  );
}
