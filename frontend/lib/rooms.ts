// Room display helpers: no runtime imports, shared by the list and room view.
export function roomHref(id: number | "new"): string { return `/chat?room=${id}`; }
export function parseRoom(raw: string | null): number | "new" | null {
  if (raw === "new") return raw;
  return raw && /^[1-9]\d*$/.test(raw) && Number.isSafeInteger(Number(raw)) ? Number(raw) : null;
}
export function roomMemberName(members: { agent_id: number; name: string }[], id: number): string {
  return members.find(member => member.agent_id === id)?.name ?? `Agent ${id}`;
}
export function queueText(members: { agent_id: number; name: string }[], entries: { agent_id: number; status: string }[]): string {
  return entries.filter(entry => ["queued", "running", "needs_input"].includes(entry.status)).map(entry => `${roomMemberName(members, entry.agent_id)} ${entry.status === "running" ? "is working" : entry.status === "needs_input" ? "is waiting for approval" : "is queued"}`).join(" · ");
}
export function mentionToken(name: string): string { return `@${name} `; }
