"use client";

import { useState } from "react";
import { ApiError, type Agent } from "@/lib/api";
import { collaboration, type Room, type RoomInput } from "@/lib/collaboration";

const FIELD = "mt-1 w-full rounded-field border border-line bg-raised px-3 py-2 text-sm";
export function RoomSettings({ room, agents, onSaved, onCancel }: { room?: Room; agents: Agent[]; onSaved: (room: Room) => void; onCancel: () => void }) {
  const [name, setName] = useState(room?.name ?? "");
  const [bulletin, setBulletin] = useState(room?.bulletin ?? "");
  const [members, setMembers] = useState<number[]>(room?.members.map(member => member.agent_id) ?? []);
  const [kind, setKind] = useState<Room["default_responder_kind"]>(room?.default_responder_kind ?? "mentions");
  const [responder, setResponder] = useState<number | null>(room?.default_responder_agent_id ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      const input: RoomInput = { name: name.trim(), bulletin, member_ids: members, default_responder_kind: kind, default_responder_agent_id: kind === "member" ? responder : null };
      const saved = room ? await collaboration.saveRoom(room.id, { name: input.name, bulletin, default_responder_kind: kind, default_responder_agent_id: input.default_responder_agent_id }) : await collaboration.createRoom(input);
      onSaved(saved);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not save this room."); }
    finally { setBusy(false); }
  };
  return <form onSubmit={save} className="space-y-4"><fieldset disabled={busy} className="space-y-4">
    <label className="block text-sm">Room name<input required maxLength={80} className={FIELD} value={name} onChange={event => setName(event.target.value)} /></label>
    {!room && <fieldset className="space-y-2"><legend className="mb-2 text-sm">Members</legend>{agents.filter(agent => !agent.archived_at).map(agent => <label key={agent.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={members.includes(agent.id)} onChange={event => setMembers(previous => event.target.checked ? [...previous, agent.id] : previous.filter(id => id !== agent.id))} />{agent.name}</label>)}<p className="text-xs text-muted">Choose up to 12 agents. They answer one at a time.</p></fieldset>}
    <label className="block text-sm">Shared instructions<textarea maxLength={12000} rows={4} className={FIELD} value={bulletin} onChange={event => setBulletin(event.target.value)} /><span className="mt-1 block text-xs text-muted">Visible to every room member. Do not put credentials here.</span></label>
    <label className="block text-sm">Who answers without a tag?<select className={FIELD} value={kind} onChange={event => setKind(event.target.value as Room["default_responder_kind"])}><option value="mentions">Only tagged members</option><option value="member">One member</option><option value="everyone">Everyone</option></select></label>
    {kind === "member" && <label className="block text-sm">Default responder<select required className={FIELD} value={responder ?? ""} onChange={event => setResponder(Number(event.target.value) || null)}><option value="">Choose a member…</option>{agents.filter(agent => members.includes(agent.id)).map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>}
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    <div className="flex gap-2"><button className="rounded-field border border-line px-3 py-2 text-sm hover:bg-hover" type="submit" disabled={!name.trim() || members.length === 0 || members.length > 12 || (kind === "member" && !responder)}>{busy ? "Saving…" : room ? "Save room" : "Create room"}</button><button className="px-3 py-2 text-sm underline" type="button" onClick={onCancel}>Cancel</button></div>
  </fieldset></form>;
}
