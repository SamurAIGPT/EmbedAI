// Turns a thread's events into what the chat shows. Pure: no React, no network.
//
// Stored events arrive in order with a seq. Applying one that is not newer than
// the last is a no-op, so a replay after a reconnect can never show anything
// twice. Typing text (text.delta) has no seq and is never stored: it is a
// preview that the stored assistant.message replaces.
import type { ThreadEvent } from "./api";
import type { RecalledNote, RecalledPassage } from "./memory";

export type ApprovalStatus = "pending" | "allowed" | "denied" | "expired" | "cancelled";

export type TurnEndStatus = "completed" | "failed" | "cancelled" | "interrupted";

// A file attached to a message: metadata only, the bytes are fetched by id.
export type AttachmentRef = { id: string; name: string; mime: string; bytes: number; kind: string };

export type TranscriptItem =
  | { kind: "user"; key: string; turnId: number | null; text: string; attachments: AttachmentRef[] }
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
      // "stopped": the turn ended (stopped, failed, interrupted) before the call finished.
      state: "running" | "done" | "error" | "stopped";
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
      // "saved_rule" when a saved "always allow" rule answered it, not a
      // person; "" otherwise. `ruleId` is that rule.
      resolvedBy: string;
      ruleId: number | null;
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
      // How far an interrupted turn had got: before_accept, unknown, after_accept ("" if not said).
      replay: string;
      // What the engine reported it used, or null when it reported nothing.
      usage: Record<string, unknown> | null;
    }
  | { kind: "session_reset"; key: string; message: string }
  // What was recalled and put in front of the person's message (docs/MEMORY.md).
  | { kind: "recalled"; key: string; turnId: number | null; notes: RecalledNote[]; conversations: RecalledPassage[] }
  // The turn moved to the agent's fallback engine because its own was limited.
  | { kind: "engine_switched"; key: string; turnId: number | null; from: string; to: string; model: string; message: string }
  // Earlier messages were folded into a summary for an engine that replays the transcript.
  | { kind: "compacted"; key: string; turnId: number | null; folded: number; summary: string };

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

function attachmentRefs(value: unknown): AttachmentRef[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(record)
    .filter((a) => typeof a.id === "string" && /^[0-9a-f]{32}$/.test(a.id))
    .map((a) => ({
      id: a.id as string,
      name: text(a.name) || "attachment",
      mime: text(a.mime),
      bytes: typeof a.bytes === "number" ? a.bytes : 0,
      kind: text(a.kind),
    }));
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

// A turn that ended cannot be waiting for anyone, and cannot still be running a
// tool: its open approvals are closed and its unfinished tool steps marked.
function closeTurn(items: TranscriptItem[], forTurn: number | null): TranscriptItem[] {
  const open = (i: TranscriptItem) =>
    (i.kind === "approval" && i.turnId === forTurn && i.status === "pending") ||
    (i.kind === "tool" && i.turnId === forTurn && i.state === "running");
  if (!items.some(open)) return items;
  return items.map((i) => {
    if (!open(i)) return i;
    return i.kind === "approval"
      ? { ...i, status: "cancelled" as const }
      : { ...i, state: "stopped" as const };
  });
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
        items: [
          ...state.items,
          { kind: "user", key, turnId: id, text: text(data.prompt_text), attachments: attachmentRefs(data.attachments) },
        ],
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
            resolvedBy: "",
            ruleId: null,
          },
        ],
      };

    case "approval.resolved": {
      const approvalId = text(data.approval_id);
      const status = approvalStatus(data.status);
      const resolvedBy = text(data.by);
      const ruleId = typeof data.rule_id === "number" ? data.rule_id : null;
      return {
        ...base,
        items: state.items.map((i) =>
          i.kind === "approval" && i.approvalId === approvalId && i.status === "pending"
            ? { ...i, status, resolvedBy, ruleId }
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
          ...closeTurn(state.items, id),
          {
            kind: "turn_end",
            key,
            turnId: id,
            status,
            error: text(data.error),
            errorCode: text(data.error_code),
            reason: text(data.reason),
            replay: text(data.replay),
            usage: Object.keys(record(data.usage)).length ? record(data.usage) : null,
          },
        ],
      };
    }

    case "session.reset":
      return {
        ...base,
        items: [...state.items, { kind: "session_reset", key, message: text(data.message) }],
      };

    case "memory.recalled": {
      const list = (value: unknown) => (Array.isArray(value) ? value.map(record) : []);
      const notes = list(data.notes).map((n) => ({ name: text(n.name), title: text(n.title) || null, snippet: text(n.snippet) }));
      const conversations = list(data.conversations).map((c) => ({
        thread_id: typeof c.thread_id === "number" ? c.thread_id : 0,
        title: text(c.title) || null,
        role: text(c.role),
        at: text(c.at) || null,
        snippet: text(c.snippet),
      }));
      return { ...base, items: [...state.items, { kind: "recalled", key, turnId: id, notes, conversations }] };
    }

    case "engine.switched":
      return {
        ...base,
        items: [
          ...state.items,
          {
            kind: "engine_switched",
            key,
            turnId: id,
            from: text(data.from_label) || text(data.from),
            to: text(data.to_label) || text(data.to),
            model: text(data.model),
            message: text(data.message),
          },
        ],
      };

    case "context.compacted":
      return {
        ...base,
        items: [
          ...state.items,
          {
            kind: "compacted",
            key,
            turnId: id,
            folded: typeof data.folded_messages === "number" ? data.folded_messages : 0,
            summary: text(data.summary),
          },
        ],
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
