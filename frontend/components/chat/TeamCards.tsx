"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import { collaboration } from "@/lib/collaboration";
import type { TeamJobItem, TranscriptItem } from "@/lib/transcript";
import { JobCard } from "./TeamPanel";
import { useSession } from "@/components/AuthGate";

export function DelegationCard({ item }: { item: TeamJobItem }) {
  return <JobCard compact job={{ id: item.jobId, root_id: null, parent_id: null, depth: 0, requester_agent_id: 0, assignee_agent_id: item.agentId, assignee_thread_id: item.threadId, mode: item.mode, brief: item.brief, status: item.status, result: item.result, outcome: item.outcome }} />;
}
export function AgentCreatedCard({ item }: { item: Extract<TranscriptItem, { kind: "agent_created" }> }) {
  const [state, setState] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { session } = useSession();
  const undo = async () => {
    setState("busy"); setError(null);
    try { const result = await collaboration.undoCreate(item.agentId); setState(result.removed); }
    catch (err) { setState(""); setError(err instanceof ApiError ? err.message : "Could not undo this specialist."); }
  };
  return <article className="space-y-2 rounded-field border border-line p-3 text-sm"><p><strong>Specialist created: {item.name || `Agent ${item.agentId}`}</strong>{item.title ? ` · ${item.title}` : ""}</p><p className="text-xs text-muted">Instructions were written by the lead. Review them in Agents.</p>{session?.scopes.includes("admin") && !state && <button type="button" className="text-xs underline" onClick={() => void undo()}>Undo creation</button>}{state && <p role="status" className="text-xs text-muted">{state === "busy" ? "Undoing…" : `Specialist ${state}.`}</p>}{error && <p role="alert" className="text-xs text-danger">{error}</p>}</article>;
}

const STALE: Record<string, string> = {
  changed_since: "Something changed since this was applied, so it was left alone.",
  agent_used: "A specialist this added has already been used, so it was kept.",
  agent_busy: "A teammate is working right now. Try again when it is idle.",
};
const VERB: Record<string, string> = { created: "Added", updated: "Updated", deleted: "Deleted" };

export function TeamSetupCard({ item }: { item: Extract<TranscriptItem, { kind: "team_setup" }> }) {
  const [state, setState] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { session } = useSession();
  const undo = async () => {
    setState("busy"); setError(null);
    try { await collaboration.undoSetup(item.requestId); setState("undone"); }
    catch (err) { setState(""); setError(err instanceof ApiError ? STALE[err.code ?? ""] ?? err.message : "Could not undo this change."); }
  };
  const canUndo = item.undoAvailable && !state && session?.scopes.includes("admin");
  return (
    <article className="space-y-2 rounded-field border border-line p-3 text-sm">
      <p><strong>Team updated by the lead</strong></p>
      <ul className="text-xs text-muted">{item.rows.map((row) => <li key={`${row.action}-${row.agentId}`}>{VERB[row.action] ?? row.action}: {row.name || `Agent ${row.agentId}`}</li>)}</ul>
      {canUndo && <button type="button" className="text-xs underline" onClick={() => void undo()}>Undo</button>}
      {state && <p role="status" className="text-xs text-muted">{state === "busy" ? "Undoing…" : "Undone."}</p>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </article>
  );
}
