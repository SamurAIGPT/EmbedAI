"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, desktopViewerUrl } from "@/lib/api";
import { DesktopRfb, DESKTOP_KEYS } from "@/lib/desktop-rfb";
import { takeover } from "@/lib/takeover";

const BUTTON = "cursor-pointer rounded border border-line px-2 py-1 text-xs disabled:opacity-50";
export function LiveDesktop({ computerId }: { computerId: number }) {
  const [state, dispatch] = useReducer(takeover, "watching");
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [expanded, setExpanded] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const lease = useRef<string | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const client = useRef<DesktopRfb | null>(null);
  const mounted = useRef(false);
  const generation = useRef(0);
  const active = state !== "watching" && state !== "taking";

  const release = useCallback(() => {
    generation.current++;
    const token = lease.current;
    lease.current = null;
    socket.current?.close(); socket.current = null; client.current = null;
    if (token) void api.releaseComputerControl(computerId, token).catch(() => {});
    dispatch("release"); setExpanded(false);
    opener.current?.focus();
  }, [computerId]);
  useEffect(() => {
    mounted.current = true;
    window.addEventListener("pagehide", release);
    return () => { mounted.current = false; window.removeEventListener("pagehide", release); release(); };
  }, [release]);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      if (lease.current) void api.computerControlHeartbeat(computerId, lease.current).catch(() => { setError("Control was handed back or this view lost its lease."); release(); });
    }, 10000);
    return () => clearInterval(timer);
  }, [active, computerId, release]);

  const connect = (ticket: string, version: number) => {
    socket.current?.close();
    const ws = new WebSocket(desktopViewerUrl(computerId, ticket, location.href), "binary");
    ws.binaryType = "arraybuffer"; socket.current = ws;
    let firstFrame = false;
    let refresh: ReturnType<typeof setTimeout>;
    const timeout = setTimeout(() => { if (!firstFrame && version === generation.current) { setError("The desktop could not be shown. Try again."); release(); } }, 15000);
    const rfb = new DesktopRfb({
      send: (bytes) => { if (ws.readyState === WebSocket.OPEN) ws.send(bytes as Uint8Array<ArrayBuffer>); },
      ready: (width, height) => { if (canvas.current) { canvas.current.width=width; canvas.current.height=height; } },
      rectangle: (x,y,width,height,rgba) => { canvas.current?.getContext("2d")?.putImageData(new ImageData(rgba as Uint8ClampedArray<ArrayBuffer>,width,height),x,y); },
      frame: () => { firstFrame=true; clearTimeout(timeout); if (version === generation.current) dispatch("frame"); refresh=setTimeout(()=>rfb.request(),100); },
    });
    client.current = rfb;
    ws.onmessage = (event) => {
      if (version !== generation.current) return;
      try { rfb.push(new Uint8Array(event.data)); }
      catch (err) { setError(err instanceof Error ? err.message : "The desktop stream failed."); release(); }
    };
    ws.onclose = () => { clearTimeout(timeout); clearTimeout(refresh); if (version === generation.current && lease.current) { dispatch("disconnect"); setError("Connection lost. Reconnect within 20 seconds to keep control."); } };
    ws.onerror = () => { ws.close(); };
  };
  const take = async () => {
    const version = ++generation.current;
    dispatch("take"); setError(null);
    try {
      const result = await api.takeComputerControl(computerId, lease.current ?? undefined);
      const token = result.lease_id ?? lease.current;
      if (!mounted.current || generation.current !== version) { if (result.lease_id) void api.releaseComputerControl(computerId,result.lease_id).catch(()=>{}); return; }
      if (!result.owned || !token || !result.ticket) { setError("Another view has control. Ask it to hand control back."); dispatch("failed"); return; }
      lease.current = token; dispatch("taken"); connect(result.ticket,version);
    } catch (err) { if (generation.current === version) { setError(err instanceof Error ? err.message : "Could not take control."); release(); } }
  };
  const pointer = (event: React.PointerEvent<HTMLCanvasElement>, buttons=event.buttons) => {
    const rect=event.currentTarget.getBoundingClientRect();
    const mask=(buttons&1 ? 1:0) | (buttons&4 ? 2:0) | (buttons&2 ? 4:0);
    client.current?.pointer((event.clientX-rect.left)*event.currentTarget.width/rect.width,(event.clientY-rect.top)*event.currentTarget.height/rect.height,mask);
  };
  return <section className={expanded ? "fixed inset-0 z-50 flex flex-col gap-2 bg-inset p-3" : "space-y-2"}>
    <div className="flex flex-wrap items-center gap-2">
      {state === "watching" || state === "taking" ? <button ref={opener} className={BUTTON} disabled={state === "taking"} onClick={() => void take()}>{state === "taking" ? "Taking control…" : "Take control"}</button> : <>
        <span className="text-xs">{state === "live" ? "You are in control" : state === "lost" ? "Connection lost" : "Connecting…"}</span>
        <button className={BUTTON} onClick={release}>Hand control back</button>
        <button className={BUTTON} onClick={() => setExpanded(!expanded)}>{expanded ? "Exit full screen" : "Full screen"}</button>
        {state === "lost" && <button className={BUTTON} onClick={() => void take()}>Reconnect</button>}
      </>}
    </div>
    {active && <>
      <canvas ref={canvas} tabIndex={0} role="application" aria-label="Computer desktop. Click to focus; Hand control back returns control to the agent." className="aspect-[16/10] min-h-0 w-full touch-none rounded border border-line bg-black object-contain outline-accent"
        onPointerDown={event => { event.preventDefault(); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId); pointer(event); }}
        onPointerMove={event => pointer(event)} onPointerUp={event => pointer(event,0)} onPointerCancel={event => pointer(event,0)} onContextMenu={event => event.preventDefault()}
        onWheel={event => { event.preventDefault(); const rect=event.currentTarget.getBoundingClientRect(); const x=(event.clientX-rect.left)*event.currentTarget.width/rect.width,y=(event.clientY-rect.top)*event.currentTarget.height/rect.height; client.current?.pointer(x,y,event.deltaY<0 ? 8:16); client.current?.pointer(x,y,0); }}
        onKeyDown={event => { const key=DESKTOP_KEYS[event.key] ?? (event.key.length === 1 ? event.key.codePointAt(0) : undefined); if (key) { event.preventDefault(); client.current?.key(key,true); } }}
        onKeyUp={event => { const key=DESKTOP_KEYS[event.key] ?? (event.key.length === 1 ? event.key.codePointAt(0) : undefined); if (key) { event.preventDefault(); client.current?.key(key,false); } }} />
      <form className="flex flex-wrap gap-2" onSubmit={event => { event.preventDefault(); client.current?.text(text); setText(""); }}>
        <input aria-label="Text to type on the desktop" maxLength={2000} className="min-w-0 flex-1 rounded border border-line bg-transparent px-2 py-1 text-sm" value={text} onChange={event=>setText(event.target.value)} />
        <button type="submit" className={BUTTON}>Type text</button>
        {["Escape","Tab","Enter"].map(key=><button type="button" key={key} className={BUTTON} onClick={()=>{client.current?.key(DESKTOP_KEYS[key],true);client.current?.key(DESKTOP_KEYS[key],false);}}>{key}</button>)}
      </form>
    </>}
    {error && <p role="status" className="text-xs text-warning">{error}</p>}
  </section>;
}
