"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api";
import { collaboration, type Room } from "@/lib/collaboration";
import { roomHref } from "@/lib/rooms";
import { useSession } from "@/components/AuthGate";

export function RoomList({ selected, onTeam, onNavigate }: { selected: number | "new" | null; onTeam: () => void; onNavigate?: () => void }) {
  const [rooms, setRooms] = useState<Room[] | null>(null);
  const [teamReady, setTeamReady] = useState(false);
  const { session } = useSession();
  const admin = session?.scopes.includes("admin") === true;
  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      if (document.hidden) return;
      collaboration.rooms().then(value => { if (!cancelled) setRooms(value); }).catch(err => { if (!cancelled && err instanceof ApiError && err.status === 404) setRooms(null); });
      collaboration.team().then(() => { if (!cancelled) setTeamReady(true); }).catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, 5000);
    document.addEventListener("visibilitychange", refresh);
    return () => { cancelled = true; clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, []);
  if (rooms === null && !teamReady) return null;
  return <section aria-label="Rooms and team" className="space-y-1 py-2">
    {teamReady && <button type="button" className="w-full rounded-field px-3 py-2 text-left text-sm font-medium hover:bg-hover" onClick={() => { onTeam(); onNavigate?.(); }}>Team</button>}
    {rooms !== null && <><div className="flex items-center justify-between px-3 pt-2"><h2 className="text-xs font-semibold uppercase tracking-wide text-muted">Rooms</h2>{admin && <Link href={roomHref("new")} aria-label="New room" className="rounded p-2 text-sm hover:bg-hover" onClick={onNavigate}>+</Link>}</div>
      {rooms.length === 0 && <p className="px-3 text-xs text-muted">No rooms yet.</p>}
      <ul>{rooms.map(room => <li key={room.id}><Link href={roomHref(room.id)} onClick={onNavigate} aria-current={selected === room.id ? "page" : undefined} className={`flex items-center gap-2 rounded-field px-3 py-2 text-sm hover:bg-hover ${selected === room.id ? "bg-hover" : ""}`}><span className="min-w-0 flex-1 truncate">{room.name}</span>{room.dispatches.some(item => item.status === "running") && <span className="animate-pulse text-xs text-warning">working</span>}{room.unread > 0 && <span className="rounded-full bg-accent px-1.5 text-xs text-white" aria-label={`${room.unread} unread messages`}>{room.unread}</span>}</Link></li>)}</ul></>}
  </section>;
}
