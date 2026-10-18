"use client";

import { useEffect, useRef } from "react";
import type { Transcript } from "@/lib/transcript";
import {
  ApprovalCard,
  AssistantMessage,
  SessionReset,
  RecalledChip,
  CompactedMarker,
  ToolStep,
  type Remembered,
  TurnEnd,
  UserMessage,
} from "./items";

const STICK_DISTANCE_PX = 80;

// The transcript, oldest first. It follows new content only while the person is
// at the bottom: scrolling up to read leaves the view where it is.
export function Conversation({
  transcript,
  answering,
  approvalErrors,
  onAnswer,
  remembered = {},
  undoneRules = [],
  onUndo,
  threadId = null,
  emptyHint,
}: {
  // The conversation the transcript belongs to: attachments are fetched from it.
  threadId?: number | null;
  // What to say when there is nothing yet (depends on whether an engine is ready).
  emptyHint?: string;
  transcript: Transcript;
  answering: string | null;
  approvalErrors: Record<string, string>;
  onAnswer: (approvalId: string, decision: "allow" | "deny", remember?: boolean) => void;
  // "Always allow" outcomes by approval id, from answers given on this page.
  remembered?: Record<string, Remembered>;
  undoneRules?: number[];
  onUndo?: (ruleId: number) => void;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const stuck = useRef(true);

  useEffect(() => {
    const el = scroller.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [transcript.items.length, transcript.typing, transcript.runningTurnId]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_DISTANCE_PX;
  };

  const working = transcript.runningTurnId !== null && !transcript.typing;
  const waitingOnYou = transcript.items.some((i) => i.kind === "approval" && i.status === "pending");

  return (
    <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-6">
        {transcript.items.length === 0 && transcript.typing === null && (
          <div className="py-16 text-center text-sm text-zinc-500">
            <p>{emptyHint ?? "Ask your agent something."}</p>
            <p className="mt-2 text-xs">
              History stays on this computer. Attach images or text files with 📎, paste, or drop them on the message box.
            </p>
          </div>
        )}

        {transcript.items.map((item) => {
          switch (item.kind) {
            case "user":
              return <UserMessage key={item.key} item={item} threadId={threadId} />;
            case "assistant":
              return <AssistantMessage key={item.key} text={item.text} incomplete={item.incomplete} />;
            case "tool":
              return <ToolStep key={item.key} item={item} />;
            case "approval":
              return (
                <ApprovalCard
                  key={item.key}
                  item={item}
                  answering={answering === item.approvalId}
                  error={approvalErrors[item.approvalId] ?? null}
                  onAnswer={(decision, remember) => onAnswer(item.approvalId, decision, remember)}
                  remembered={remembered[item.approvalId]}
                  undoneRules={undoneRules}
                  onUndo={onUndo}
                />
              );
            case "turn_end":
              return <TurnEnd key={item.key} item={item} />;
            case "session_reset":
              return <SessionReset key={item.key} item={item} />;
            case "recalled":
              return <RecalledChip key={item.key} item={item} />;
            case "compacted":
              return <CompactedMarker key={item.key} item={item} />;
          }
        })}

        {transcript.typing !== null && transcript.typing !== "" && (
          <AssistantMessage text={transcript.typing} live />
        )}

        {working && !waitingOnYou && (
          <p className="animate-pulse text-xs text-zinc-500">Working…</p>
        )}
      </div>
    </div>
  );
}
