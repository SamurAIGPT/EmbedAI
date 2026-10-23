import { request, type Agent } from "@/lib/api";

export type TeamAgent = Agent & { is_lead: boolean; state: string; delegation_enabled: boolean; created_by_agent_id?: number | null; instructions_reviewed?: boolean };
export type TeamState = { team: { id: number; name: string; lead_agent_id: number | null } | null; agents: TeamAgent[]; limits: Record<string, number>; lead_ready: { ok: boolean; reason?: string | null } };
export type SharedApproval = { id: string; thread_id: number; turn_id: number; tool: string; summary: string | null; input: Record<string, unknown>; kind: string | null; status: "pending" | "allowed" | "denied" | "expired" | "cancelled" };
export type TeamJob = { id: number; root_id: number | null; parent_id: number | null; depth: number; requester_agent_id: number; assignee_agent_id: number; assignee_thread_id: number | null; mode: string; brief: string; status: string; result?: string | null; outcome?: string | null; outcome_detail?: string | null; children?: TeamJob[]; approvals?: SharedApproval[] };
export type RoomMember = { agent_id: number; name: string; held: boolean; member_thread_id: number | null };
export type RoomDispatch = { id: number; agent_id: number; status: string; reason: string; turn_id: number | null; error_code: string | null };
export type Room = { id: number; name: string; bulletin: string; thread_id: number; default_responder_kind: "member" | "everyone" | "mentions"; default_responder_agent_id: number | null; last_read_seq: number; last_seq: number; unread: number; members: RoomMember[]; dispatches: RoomDispatch[] };
export type RoomMessage = { seq: number; text: string; author: { kind: string; agent_id?: number; label?: string }; source?: { thread_id?: number; routine_id?: number } | null; created_at: string };
export type RoomInput = { name: string; member_ids: number[]; bulletin?: string; default_responder_kind?: Room["default_responder_kind"]; default_responder_agent_id?: number | null };
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const patch = (body: unknown): RequestInit => ({ method: "PATCH", body: JSON.stringify(body) });

export const collaboration = {
  team: () => request<TeamState>("/team"),
  lead: (agent_id: number) => request<TeamState>("/team/lead", post({ agent_id })),
  addMember: (agent_id: number) => request<unknown>("/team/members", post({ agent_id })),
  removeMember: (id: number) => request<void>(`/team/members/${id}`, { method: "DELETE" }),
  delegation: (id: number, delegation_enabled: boolean) => request<unknown>(`/team/members/${id}`, patch({ delegation_enabled })),
  undoCreate: (id: number) => request<{ removed: string }>(`/team/agents/${id}/undo-create`, post({})),
  jobs: () => request<TeamJob[]>("/team/jobs"),
  job: (id: number) => request<TeamJob>(`/team/jobs/${id}`),
  cancelJob: (id: number) => request<void>(`/team/jobs/${id}/cancel`, post({})),
  activity: () => request<{ id: number; kind: string; agent_id?: number; job_id?: number; created_at: string }[]>("/team/activity"),
  rooms: () => request<Room[]>("/rooms"),
  room: (id: number) => request<Room>(`/rooms/${id}`),
  createRoom: (input: RoomInput) => request<Room>("/rooms", post(input)),
  saveRoom: (id: number, input: Partial<RoomInput>) => request<Room>(`/rooms/${id}`, patch(input)),
  deleteRoom: (id: number) => request<void>(`/rooms/${id}`, { method: "DELETE" }),
  messages: (id: number) => request<RoomMessage[]>(`/rooms/${id}/messages`),
  postMessage: (id: number, text: string) => request<{ seq: number }>(`/rooms/${id}/messages`, post({ text })),
  readRoom: (id: number, seq: number) => request<void>(`/rooms/${id}/read`, post({ seq })),
  holdMember: (room: number, agent: number, held: boolean) => request<unknown>(`/rooms/${room}/members/${agent}`, patch({ held })),
  stopRoom: (room: number, agent_id?: number) => request<void>(`/rooms/${room}/stop`, post({ agent_id })),
  retryDispatch: (room: number, dispatch: number) => request<unknown>(`/rooms/${room}/dispatches/${dispatch}/retry`, post({})),
  roomApprovals: (id: number) => request<SharedApproval[]>(`/rooms/${id}/approvals`),
};
