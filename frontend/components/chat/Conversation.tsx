"use client";

import { ArrowDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Transcript, TranscriptItem } from "@/lib/transcript";
import { announcement, groupToolRuns } from "@/lib/transcript-view";
import { Avatar } from "@/components/ui/Avatar";
import {
  ApprovalCard,
  AssistantMessage,
  SessionReset,
  RecalledChip,
  CompactedMarker,
  EngineSwitched,
  ToolRun,
  ToolStep,
  type Remembered,
  TurnEnd,
  UserMessage,
} from "./items";

import { DelegationCard, AgentCreatedCard } from "./TeamCards";

const STICK_DISTANCE_PX = 80;

// Says the few things worth hearing (an approval waiting, a finished answer, a failure) once,
// in a visually hidden polite region. The transcript itself is not live: it would be re-read on
// every tool update.
function Announcer({ items }: { items: Transcript["items"] }) {
  const [text, setText] = useState("");
  const spoken = useRef<string | null>(null);
  useEffect(() => {
    const next = announcement(items);
    if (next && next.key !== spoken.current) {
      // The first look at a conversation is history, not news: remember it without speaking.
      if (spoken.current !== null) setText(next.text);
      spoken.current = next.key;
    } else if (!next && spoken.current === null) {
      spoken.current = "";
    }
  }, [items]);
  return <div role="status" aria-live="polite" className="sr-only">{text}</div>;
}

// The transcript, oldest first. It follows new content only while the person is
// at the bottom: scrolling up to read leaves the view where it is, and a button brings
// them back.
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
  agent = null,
}: {
  // The conversation the transcript belongs to: attachments are fetched from it.
  threadId?: number | null;
  // What to say when there is nothing yet (depends on whether an engine is ready).
  emptyHint?: string;
  // Who the conversation is with, for the empty state; null for a plain chat.
  agent?: { id: number; name: string } | null;
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
  const [away, setAway] = useState(false);

  useEffect(() => {
    const el = scroller.current;
    if (el && stuck.current) el.scrollTop = el.scrollHeight;
  }, [transcript.items.length, transcript.typing, transcript.runningTurnId]);

  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_DISTANCE_PX;
    setAway((previous) => (previous === !stuck.current ? previous : !stuck.current));
  };

  const jumpToLatest = () => {
    const el = scroller.current;
    if (!el) return;
    stuck.current = true;
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  const working = transcript.runningTurnId !== null && !transcript.typing;
  const waitingOnYou = transcript.items.some((i) => i.kind === "approval" && i.status === "pending");
  const empty = transcript.items.length === 0 && transcript.typing === null;

  return (
    <div className="relative min-h-0 flex-1">
      <Announcer items={transcript.items} />
      <div ref={scroller} onScroll={onScroll} className="h-full overflow-y-auto">
        <div role="log" aria-live="off" aria-label="Conversation" className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6 sm:px-6">
          {empty && (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <Avatar agentId={agent?.id ?? null} name={agent?.name} size="lg" />
              <p className="text-lg font-semibold">{agent ? `Ask ${agent.name} something` : "Ask your agent something"}</p>
              {emptyHint && <p className="max-w-md text-sm text-muted">{emptyHint}</p>}
              <p className="max-w-md text-xs text-faint">
                History stays on this computer. Attach images or text files with the paperclip, paste, or drop them on the message box.
              </p>
            </div>
          )}

          {groupToolRuns(transcript.items).map((item) => {
            switch (item.kind) {
              case "team_job":
                return <DelegationCard key={item.key} item={item} />;
              case "agent_created":
                return <AgentCreatedCard key={item.key} item={item} />;
              case "tool_run":
                return <ToolRun key={item.key} tools={item.tools as Extract<TranscriptItem, { kind: "tool" }>[]} />;
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
              case "engine_switched":
                return <EngineSwitched key={item.key} item={item} />;
            }
          })}

          {transcript.typing !== null && transcript.typing !== "" && <AssistantMessage text={transcript.typing} live />}

          {working && !waitingOnYou && (
            <p className="flex items-center gap-1.5 text-xs text-muted" aria-label="Working">
              <span className="inline-flex gap-1" aria-hidden="true">
                <span className="size-1.5 animate-pulse rounded-full bg-faint" />
                <span className="size-1.5 animate-pulse rounded-full bg-faint [animation-delay:150ms]" />
                <span className="size-1.5 animate-pulse rounded-full bg-faint [animation-delay:300ms]" />
              </span>
              Working…
            </p>
          )}
        </div>
      </div>
      {away && (
        <button
          type="button"
          onClick={jumpToLatest}
          aria-label="Jump to latest"
          title="Jump to latest"
          className="absolute bottom-3 left-1/2 inline-flex size-9 -translate-x-1/2 cursor-pointer animate-pop-in items-center justify-center rounded-full border border-line bg-raised text-ink shadow-lg hover:bg-hover touch:size-11"
        >
          <ArrowDown size={16} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
