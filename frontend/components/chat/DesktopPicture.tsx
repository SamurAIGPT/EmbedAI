"use client";

import { useEffect, useState } from "react";
import { api, type Computer } from "@/lib/api";

export function DesktopPicture({ computer }: { computer: Computer }) {
  const [picture, setPicture] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const running = (computer.live_state ?? computer.state) === "running";
  useEffect(() => {
    let active = true;
    let url: string | null = null;
    let timer: ReturnType<typeof setTimeout>;
    const capture = async () => {
      if (document.visibilityState === "visible" && running) {
        try {
          const next = await api.computerScreenshot(computer.id);
          if (!active) { URL.revokeObjectURL(next); return; }
          if (url) URL.revokeObjectURL(url);
          url = next;
          setPicture(next);
          setError(null);
        } catch (err) {
          if (active) setError(err instanceof Error ? err.message : "The screen could not be shown.");
        }
      }
      if (active) timer = setTimeout(capture, computer.busy ? 4000 : 30000);
    };
    void capture();
    const visible = () => { if (document.visibilityState === "visible") { clearTimeout(timer); void capture(); } };
    document.addEventListener("visibilitychange", visible);
    return () => { active = false; clearTimeout(timer); document.removeEventListener("visibilitychange", visible); if (url) URL.revokeObjectURL(url); };
  }, [computer.id, computer.busy, running]);
  useEffect(() => {
    if (!expanded) return;
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setExpanded(false); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [expanded]);
  return <section className={expanded ? "fixed inset-0 z-50 flex flex-col bg-inset p-4" : "space-y-2"}>
    <div className="flex items-center justify-between gap-2 text-xs">
      <span>{running ? "Desktop preview" : "Start the computer to see its screen"}</span>
      <button className="rounded border border-line px-2 py-1" onClick={() => setExpanded(!expanded)}>{expanded ? "Exit full screen" : "Full screen"}</button>
    </div>
    <div role="img" aria-label={running ? "Desktop screen preview" : "Desktop stopped"} className="relative flex aspect-[16/10] min-h-0 items-center justify-center overflow-hidden rounded border border-line bg-black">
      {/* Blob URLs require a normal image rather than Next image optimization. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {picture && running ? <img src={picture} alt="Current desktop" className="h-full w-full object-contain" /> : <p className="p-4 text-sm text-white">{running ? "The desktop is starting…" : "Computer stopped"}</p>}
    </div>
    {error && <p role="status" className="text-xs text-warning">{error}</p>}
  </section>;
}
