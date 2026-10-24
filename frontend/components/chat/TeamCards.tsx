"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
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
  const [available, setAvailable] = useState(false);
  const { session } = useSession();
  useEffect(() => {
    let live = true;
    collaboration.undoState(item.agentId).then(r => { if (live) setAvailable(r.available); }, () => { if (live) setAvailable(false); });
    return () => { live = false; };
  }, [item.agentId]);
  const undo = async () => {
    setState("busy"); setError(null);
    try { const result = await collaboration.undoCreate(item.agentId); setState(result.removed); }
    catch (err) { setState(""); setError(err instanceof ApiError ? err.message : "Could not undo this specialist."); }
  };
  return <article className="space-y-2 rounded-field border border-line p-3 text-sm"><p><strong>Specialist created: {item.name || `Agent ${item.agentId}`}</strong>{item.title ? ` · ${item.title}` : ""}</p><p className="text-xs text-muted">Instructions were written by the lead. Review them in Agents.</p>{session?.scopes.includes("admin") && available && !state && <button type="button" className="text-xs underline" onClick={() => void undo()}>Undo creation</button>}{state && <p role="status" className="text-xs text-muted">{state === "busy" ? "Undoing…" : `Specialist ${state}.`}</p>}{error && <p role="alert" className="text-xs text-danger">{error}</p>}</article>;
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
  const [availability, setAvailability] = useState<{ available: boolean; reason: string | null } | null>(null);
  useEffect(() => {
    if (!session?.scopes.includes("admin")) return;
    let live = true;
    const refresh = () => collaboration.setupReceipt(item.requestId).then(result => {
      if (live) setAvailability({ available: result.undo_available, reason: result.undo_reason });
    }, () => { if (live) setAvailability({ available: false, reason: "Could not check Undo availability." }); });
    void refresh();
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
    window.addEventListener("focus", refresh);
    return () => { live = false; clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [item.requestId, session]);
  const undo = async () => {
    setState("busy"); setError(null);
    try { await collaboration.undoSetup(item.requestId); setState("undone"); }
    catch (err) { setState(""); setError(err instanceof ApiError ? STALE[err.code ?? ""] ?? err.message : "Could not undo this change."); }
  };
  const canUndo = availability?.available === true && !state && session?.scopes.includes("admin");
  return (
    <article className="space-y-2 rounded-field border border-line p-3 text-sm">
      <p><strong>Team changes applied</strong></p>
      <ul className="space-y-2 text-xs text-muted">{item.rows.map((row) => <li key={`${row.action}-${row.agentId}`}>
        {VERB[row.action] ?? row.action}: {row.name || `Agent ${row.agentId}`}
        {row.changes && <details className="mt-1"><summary className="cursor-pointer underline">Details</summary><dl className="mt-2 space-y-2">{row.changes.map(change => <div key={change.field}><dt className="font-medium">{change.field.replaceAll("_", " ")}</dt><dd className="whitespace-pre-wrap break-words"><del>{change.before == null ? "Default / empty" : String(change.before)}</del>{" → "}{change.after == null ? "Default / empty" : String(change.after)}</dd></div>)}</dl><p className="mt-2">New conversations use saved model defaults. App connections, skills, computers and routines require their own setup.</p></details>}
        {row.action !== "deleted" && <div className="mt-1 flex gap-3"><button type="button" className="underline" onClick={() => window.dispatchEvent(new CustomEvent("open-dots:open-agent", { detail: { id: row.agentId, edit: false } }))}>Start chat</button>{session?.scopes.includes("admin") && <button type="button" className="underline" onClick={() => window.dispatchEvent(new CustomEvent("open-dots:open-agent", { detail: { id: row.agentId, edit: true } }))}>Edit agent</button>}</div>}
      </li>)}</ul>
      {item.undoAvailable && <p className="text-xs text-muted">Undo is available until a created agent is used or a changed agent is edited again. Starting its first chat can make Undo unavailable.</p>}
      {availability && !availability.available && !state && <p className="text-xs text-muted">{availability.reason}</p>}
      {canUndo && <button type="button" className="text-xs underline" onClick={() => void undo()}>Undo</button>}
      {state && <p role="status" className="text-xs text-muted">{state === "busy" ? "Undoing…" : "Undone."}</p>}
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
    </article>
  );
}

const NOTICE_LABEL = { incident: "Incident", escalation: "Escalation", refused: "Report not delivered", retried: "Retry" } as const;

// What the lead was told about a teammate (or what it retried). The text is
// quoted data from other agents; it is only ever shown, never acted on here.
export function TeamNoticeCard({ item }: { item: Extract<TranscriptItem, { kind: "team_notice" }> }) {
  return (
    <article className="flex items-start justify-between gap-3 rounded-field border border-line p-3 text-sm">
      <p className="min-w-0 break-words"><strong>{NOTICE_LABEL[item.tone]}</strong>{" · "}{item.text.replace(/^(Incident|Escalation): /, "")}</p>
      {item.threadId !== null && <Link href={`/chat?thread=${item.threadId}`} className="shrink-0 text-xs underline">Open</Link>}
    </article>
  );
}
