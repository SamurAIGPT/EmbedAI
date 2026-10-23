"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, type Agent } from "@/lib/api";
import { collaboration, type TeamState, type TeamJob } from "@/lib/collaboration";
import { useSession } from "@/components/AuthGate";
import { Dialog } from "@/components/ui/Dialog";
import { SharedApproval } from "./SharedApproval";

const BUTTON = "rounded-field border border-line px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-50";

export function JobCard({ job: initial, compact = false }: { job: TeamJob; compact?: boolean }) {
  const [job, setJob] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { session } = useSession();
  const refresh = useCallback(() => collaboration.job(initial.id).then(setJob).catch(() => {}), [initial.id]);
  useEffect(() => { void refresh(); const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 3000); return () => clearInterval(timer); }, [refresh]);
  const cancel = async () => { setBusy(true); setError(null); try { await collaboration.cancelJob(job.id); await refresh(); } catch (err) { setError(err instanceof ApiError ? err.message : "Could not stop this job."); } finally { setBusy(false); } };
  return <article className="space-y-2 rounded-field border border-line bg-raised p-3 text-sm" style={{ marginLeft: compact ? undefined : `${Math.min(job.depth ?? 0, 3) * 8}px` }}>
    <div className="flex flex-wrap items-center gap-2"><strong>Job {job.id}</strong><span className="text-xs text-muted">{job.mode} · {job.status}{job.outcome ? ` · ${job.outcome}` : ""}</span></div>
    <p className="whitespace-pre-wrap break-words">{job.brief}</p>
    {job.result && <details><summary className="cursor-pointer text-xs underline">Result</summary><p className="mt-2 whitespace-pre-wrap break-words">{job.result}</p></details>}
    {job.outcome_detail && <p className="text-xs text-warning">{job.outcome_detail}</p>}
    <div className="flex flex-wrap gap-2">{job.assignee_thread_id && <Link className={BUTTON} href={`/chat?thread=${job.assignee_thread_id}`}>Open work</Link>}{["queued", "running", "needs_input"].includes(job.status) && session?.scopes.includes("admin") && <button type="button" className={BUTTON} disabled={busy} onClick={() => void cancel()}>Stop this job</button>}</div>
    {job.approvals?.filter(item => item.status === "pending").map(approval => <SharedApproval key={approval.id} approval={approval} onChanged={() => void refresh()} />)}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </article>;
}

export function TeamPanel({ agents, onClose, onChanged }: { agents: Agent[]; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<TeamState | null>(null);
  const [jobs, setJobs] = useState<TeamJob[]>([]);
  const [activity, setActivity] = useState<{ id: number; kind: string; created_at: string }[]>([]);
  const [tab, setTab] = useState("agents");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { session } = useSession();
  const admin = session?.scopes.includes("admin") === true;
  const refresh = useCallback(async () => {
    try { const [team, list] = await Promise.all([collaboration.team(), collaboration.jobs()]); setData(team); setJobs(list); if (admin) setActivity(await collaboration.activity()); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not load the team."); }
  }, [admin]);
  useEffect(() => { const initial = setTimeout(() => void refresh(), 0); const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000); return () => { clearTimeout(initial); clearInterval(timer); }; }, [refresh]);
  const act = async (action: () => Promise<unknown>) => { setBusy(true); setError(null); try { await action(); await refresh(); onChanged(); } catch (err) { setError(err instanceof ApiError ? err.message : "Could not update the team."); } finally { setBusy(false); } };
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }} title={data?.team?.name ?? "Team"} description="A lead can create specialists and hand them work. Each agent keeps its own approvals." size="lg">
    <div className="space-y-4"><div className="flex gap-2" role="tablist" aria-label="Team view">{["agents", "activity"].map(value => <button key={value} role="tab" aria-selected={tab === value} className={BUTTON} onClick={() => setTab(value)}>{value === "agents" ? "Agents" : "Activity"}</button>)}</div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {!data && !error && <p role="status">Loading team…</p>}
      {data && tab === "agents" && <div className="space-y-3">{!data.team && <p className="text-sm text-muted">Choose a lead from your agents to start a team.</p>}{data.team && !data.lead_ready.ok && <p className="text-sm text-warning">{data.lead_ready.reason}</p>}
        {data.agents.map(agent => <article key={agent.id} className="space-y-2 rounded-field border border-line p-3"><div><strong>{agent.name}</strong>{agent.is_lead && <span className="ml-2 rounded bg-accent/15 px-2 py-0.5 text-xs">Lead</span>}<span className="ml-2 text-xs text-muted">{agent.state}</span></div><p className="text-xs text-muted">{agent.created_by_agent_id ? `Instructions written by agent ${agent.created_by_agent_id}${agent.instructions_reviewed ? " · reviewed" : " · review in Agents"}` : "Added by you"}</p>
          {admin && <div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={busy} checked={agent.delegation_enabled} onChange={event => void act(() => collaboration.delegation(agent.id, event.target.checked))} />Can delegate</label>{!agent.is_lead && <><button className={BUTTON} disabled={busy} onClick={() => void act(() => collaboration.lead(agent.id))}>Make lead</button><button className={BUTTON} disabled={busy} onClick={() => void act(() => collaboration.removeMember(agent.id))}>Remove from team</button></>}{agent.created_by_agent_id && <button className={BUTTON} disabled={busy} onClick={() => void act(() => collaboration.undoCreate(agent.id))}>Undo creation</button>}</div>}
        </article>)}
        {admin && <label className="block text-sm">{data.team ? "Add an agent" : "Choose team lead"}<select className="mt-1 w-full rounded-field border border-line bg-raised p-2" value="" disabled={busy} onChange={event => { const id = Number(event.target.value); if (id) void act(() => data.team ? collaboration.addMember(id) : collaboration.lead(id)); }}><option value="">Select an agent…</option>{agents.filter(agent => !data.agents.some(member => member.id === agent.id) && !agent.archived_at).map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>}
      </div>}
      {tab === "activity" && <div className="space-y-3">{jobs.length === 0 && <p className="text-sm text-muted">No delegated jobs yet.</p>}{jobs.map(job => <JobCard key={job.id} job={job} />)}{activity.length > 0 && <details><summary className="cursor-pointer text-sm">Team activity</summary><ol className="mt-2 space-y-1 text-xs text-muted">{activity.map(item => <li key={item.id}>{item.kind.replaceAll("_", " ")} · {new Date(item.created_at).toLocaleString()}</li>)}</ol></details>}</div>}
    </div>
  </Dialog>;
}
