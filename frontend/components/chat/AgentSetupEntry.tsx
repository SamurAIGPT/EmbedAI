"use client";

import { useEffect, useState } from "react";
import { ApiError, type Agent, type Engine } from "@/lib/api";
import { collaboration, type TeamState } from "@/lib/collaboration";

export const SETUP_STARTER = "I'd like to create an agent. Help me define its purpose, expected output and constraints. Ask only the questions you need before creating it.";

export function AgentSetupEntry({ agents, engines, onStart, onCreateLead }: {
  agents: Agent[]; engines: Engine[]; onStart: (id: number) => void; onCreateLead: () => void;
}) {
  const [team, setTeam] = useState<TeamState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [choice, setChoice] = useState("");
  useEffect(() => { let live = true; collaboration.team().then(value => { if (live) setTeam(value); }, () => { if (live) setError("Could not check your team. Reopen Agents to try again."); }); return () => { live = false; }; }, []);
  const lead = agents.find(agent => agent.id === team?.team?.lead_agent_id && !agent.archived_at);
  const engine = engines.find(item => item.id === lead?.provider);
  const ready = team?.enabled !== false && team?.lead_ready.ok && engine?.available;
  const choose = async () => {
    setBusy(true); setError(null);
    try { const updated = await collaboration.lead(Number(choice)); setTeam(updated); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not choose a lead."); }
    finally { setBusy(false); }
  };
  return <section className="space-y-2 rounded-field border border-line p-3 text-sm" aria-label="Create by chatting">
    <strong>Create by chatting</strong>
    <p className="text-xs text-muted">Describe the job to your team lead. Review the starter before sending; opening chat creates no agent.</p>
    {team?.enabled === false ? <p className="text-xs text-warning">Team tools are disabled. Enable Teams in settings, or use the manual form.</p> : lead ? <>
      {!ready && <p className="text-xs text-warning">{!engine?.available ? engine?.reason ?? "Checking the lead's engine…" : team?.lead_ready.reason} <a href="/settings/" className="underline">Engine settings</a></p>}
      <button type="button" className="rounded-field border border-line px-3 py-1.5 disabled:opacity-50" disabled={!ready || busy} onClick={() => onStart(lead.id)}>Chat with {lead.name}</button>
    </> : team && <>
      <p className="text-xs text-muted">Choose a team lead (Chief of Staff) to help create and coordinate agents. This role replaces the current lead.</p>
      <select aria-label="Choose setup lead" value={choice} disabled={busy} onChange={event => setChoice(event.target.value)} className="w-full rounded-field border border-line bg-raised p-2">
        <option value="">Choose an existing agent…</option>
        {agents.filter(agent => !agent.archived_at && engines.some(item => item.id === agent.provider && item.capabilities.mcp && item.available)).map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
      </select>
      <div className="flex flex-wrap gap-3"><button type="button" disabled={!choice || busy} className="text-xs underline disabled:opacity-50" onClick={() => void choose()}>Make selected agent lead</button><button type="button" className="text-xs underline" onClick={onCreateLead}>Create a lead in the form</button></div>
    </>}
    {!team && !error && <p role="status" className="text-xs text-muted">Checking team…</p>}
    {error && <p role="alert" className="text-xs text-danger">{error}</p>}
  </section>;
}
