import { request } from "@/lib/api";

export type PresenceResult = { returned: boolean; first_today: boolean; open: { agent_id: number; thread_id: number } | null };

// Tells the server the person is here (and their time zone). The server answers
// where today's card is only on a return or the first visit of the day.
export function reportPresence(): Promise<PresenceResult> {
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  return request<PresenceResult>("/needs-you/presence", { method: "POST", body: JSON.stringify({ time_zone: timeZone }) });
}
