// Turns a thread's events into what the chat shows. Pure: no React, no network.
//
// Stored events arrive in order with a seq. Applying one that is not newer than
// the last is a no-op, so a replay after a reconnect can never show anything
// twice. Typing text (text.delta) has no seq and is never stored: it is a
// preview that the stored assistant.message replaces.
import type { ThreadEvent } from "./api";

export type ApprovalStatus = "pending" | "allowed" | "denied" | "expired" | "cancelled";

export type TurnEndStatus = "completed" | "failed" | "cancelled" | "interrupted";

export type TranscriptItem =
  | { kind: "user"; key: string; turnId: number | null; text: string }
  | {
      kind: "assistant";
      key: string;
      turnId: number | null;
      text: string;
      // The message did not finish (the turn failed or was stopped while it was written).
      incomplete: boolean;
    }
  | {
      kind: "tool";
      key: string;
      turnId: number | null;
      callId: string;
      name: string;
      summary: string;
      input: unknown;
      state: "running" | "done" | "error";
      output: string;
      // The stored output was cut at the backend's cap.
      truncated: boolean;
    }
  | {
      kind: "approval";
      key: string;
      turnId: number | null;
      approvalId: string;
      tool: string;
      approvalKind: string;
      summary: string;
      input: Record<string, unknown>;
      status: ApprovalStatus;
    }
  | {
      kind: "turn_end";
      key: string;
      turnId: number | null;
      status: TurnEndStatus;
      error: string;
      errorCode: string;
      // Why a turn was interrupted ("shutdown", "lease_expired", ...).
      reason: string;
    }
  | { kind: "session_reset"; key: string; message: string };

export type Transcript = {
  items: TranscriptItem[];
  // The seq of the newest stored event applied; a reconnect resumes after it.
  lastSeq: number;
  // The turn that has started and not yet ended, if any.
  runningTurnId: number | null;
  // The message being typed right now, or null.
  typing: string | null;
};

export function emptyTranscript(): Transcript {
  return { items: [], lastSeq: 0, runningTurnId: null, typing: null };
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function turnId(event: ThreadEvent): number | null {
  return typeof event.turn_id === "number" ? event.turn_id : null;
}

// The backend records the person's answer as "allow" or "deny" and the other
// outcomes as "expired" and "cancelled".
function approvalStatus(value: unknown): ApprovalStatus {
  switch (value) {
    case "allow":
    case "allowed":
      return "allowed";
    case "deny":
    case "denied":
      return "denied";
    case "expired":
      return "expired";
    default:
      return "cancelled";
  }
}

const TURN_END: Record<string, TurnEndStatus> = {
  "turn.completed": "completed",
  "turn.failed": "failed",
  "turn.cancelled": "cancelled",
  "turn.interrupted": "interrupted",
};

// A turn that ended cannot be waiting for anyone: its open approvals are closed.
function closeApprovals(items: TranscriptItem[], forTurn: number | null): TranscriptItem[] {
  if (!items.some((i) => i.kind === "approval" && i.status === "pending" && i.turnId === forTurn)) {
    return items;
  }
  return items.map((i) =>
    i.kind === "approval" && i.status === "pending" && i.turnId === forTurn
      ? { ...i, status: "cancelled" as const }
      : i
  );
}

export function applyEvent(state: Transcript, event: ThreadEvent): Transcript {
  if (event.type === "text.delta") return applyTyping(state, event);
  // Only stored events carry a seq. Anything else (runtime.status) is not part
  // of the transcript.
  if (typeof event.seq !== "number") return state;
  if (event.seq <= state.lastSeq) return state;

  const key = `e${event.seq}`;
  const data = record(event.data);
  const id = turnId(event);
  const base: Transcript = { ...state, lastSeq: event.seq };

  switch (event.type) {
    case "turn.started":
      return {
        ...base,
        runningTurnId: id,
        typing: null,
        items: [...state.items, { kind: "user", key, turnId: id, text: text(data.prompt_text) }],
      };

    case "assistant.message":
      return {
        ...base,
        typing: null,
        items: [
          ...state.items,
          { kind: "assistant", key, turnId: id, text: text(data.text), incomplete: data.incomplete === true },
        ],
      };

    case "tool.started":
      return {
        ...base,
        items: [
          ...state.items,
          {
            kind: "tool",
            key,
            turnId: id,
            callId: text(data.call_id),
            name: text(data.name) || "tool",
            summary: text(data.summary),
            input: data.input ?? {},
            state: "running",
            output: "",
            truncated: false,
          },
        ],
      };

    case "tool.completed": {
      const callId = text(data.call_id);
      const state_: "done" | "error" = data.ok === false ? "error" : "done";
      const output = text(data.output);
      const truncated = data.truncated === true;
      let matched = false;
      const items = state.items.map((i) => {
        if (i.kind === "tool" && i.callId === callId && i.state === "running") {
          matched = true;
          return { ...i, state: state_, output, truncated };
        }
        return i;
      });
      if (!matched) {
        // A completion whose start was never seen: still shown, not dropped.
        items.push({
          kind: "tool",
          key,
          turnId: id,
          callId,
          name: "tool",
          summary: "",
          input: {},
          state: state_,
          output,
          truncated,
        });
      }
      return { ...base, items };
    }

    case "approval.requested":
      return {
        ...base,
        items: [
          ...state.items,
          {
            kind: "approval",
            key,
            turnId: id,
            approvalId: text(data.approval_id),
            tool: text(data.tool) || "tool",
            approvalKind: text(data.kind) || "other",
            summary: text(data.summary),
            input: record(data.input),
            status: "pending",
          },
        ],
      };

    case "approval.resolved": {
      const approvalId = text(data.approval_id);
      const status = approvalStatus(data.status);
      return {
        ...base,
        items: state.items.map((i) =>
          i.kind === "approval" && i.approvalId === approvalId && i.status === "pending"
            ? { ...i, status }
            : i
        ),
      };
    }

    case "turn.completed":
    case "turn.failed":
    case "turn.cancelled":
    case "turn.interrupted": {
      const status = TURN_END[event.type];
      return {
        ...base,
        runningTurnId: id === state.runningTurnId || id === null ? null : state.runningTurnId,
        typing: null,
        items: [
          ...closeApprovals(state.items, id),
          {
            kind: "turn_end",
            key,
            turnId: id,
            status,
            error: text(data.error),
            errorCode: text(data.error_code),
            reason: text(data.reason),
          },
        ],
      };
    }

    case "session.reset":
      return {
        ...base,
        items: [...state.items, { kind: "session_reset", key, message: text(data.message) }],
      };

    default:
      // An event type this page does not know (a later provider, a later
      // version): the cursor moves on, nothing is drawn.
      return base;
  }
}

// A typing frame: "replace" is a snapshot of the whole message so far (sent once
// when a stream connects mid-message), anything else appends.
export function applyTyping(state: Transcript, event: ThreadEvent): Transcript {
  const data = record(event.data);
  const chunk = text(data.text);
  const typing = data.mode === "replace" ? chunk : (state.typing ?? "") + chunk;
  return { ...state, typing };
}

// What the composer may do, from what the transcript knows.
export function pendingApprovals(state: Transcript) {
  return state.items.filter(
    (i): i is Extract<TranscriptItem, { kind: "approval" }> =>
      i.kind === "approval" && i.status === "pending"
  );
}
