"use client";
import { useEffect, useState } from "react";

export type NativeStatus = { supported: boolean; enabled: boolean; held: boolean; busy: boolean; ready: boolean; reason: string | null };
export type NativeRequest = { method: "status" | "enable" | "disable" | "take" | "release"; agent: number };

export function NativeComputer({ agent, supported }: { agent: number; supported: boolean }) {
  const [status, setStatus] = useState<NativeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const read = () => void window.openDots?.nativeControl({ method: "status", agent }).then(value => { if (alive) setStatus(value as NativeStatus); }).catch(() => {});
    read();
    const timer = setInterval(read, 3000);
    return () => { alive = false; clearInterval(timer); };
  }, [agent]);
  if (!status?.supported) return null;
  async function act(method: NativeRequest["method"]) {
    setBusy(true); setError(null);
    try {
      await window.openDots?.nativeControl({ method, agent });
      const value = await window.openDots?.nativeControl({ method: "status", agent });
      if (value) setStatus(value as NativeStatus);
    } catch (e) { setError(e instanceof Error ? e.message : "Native desktop unavailable."); }
    finally { setBusy(false); }
  }
  return <section className="mb-4 space-y-2 rounded-md border border-line p-3 text-sm" aria-label="Native desktop">
    <h3 className="font-medium">This Mac — native desktop</h3>
    <p className="text-xs text-muted">Optional access to apps across this Mac, including outside the workspace. Each input needs approval. When enabled, this agent uses the native desktop for new turns.</p>
    <p>{status.enabled ? (status.held ? "You have control. Agent input is stopped." : status.busy ? "A native desktop turn is active." : "Enabled for this agent.") : "Disabled for this agent. Docker computers remain available below."}</p>
    {status.reason && <p className="text-xs text-muted">{status.reason}</p>}
    {!supported && <p className="text-xs text-muted">Choose Claude to use native desktop tools.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-2">
      <button className="rounded border border-line px-2 py-1 disabled:opacity-50" disabled={busy || (!supported && !status.enabled)} onClick={() => void act(status.enabled ? "disable" : "enable")}>{status.enabled ? "Disable native desktop" : "Enable for this agent…"}</button>
      {status.enabled && <button className="rounded border border-line px-2 py-1 disabled:opacity-50" disabled={busy} onClick={() => void act(status.held ? "release" : "take")}>{status.held ? "Hand control back" : "Take control"}</button>}
    </div>
  </section>;
}
