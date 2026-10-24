"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, type Engine, type GoogleSetupOperation } from "@/lib/api";

const BUTTON = "cursor-pointer rounded-md border border-line px-3 py-2 text-sm hover:bg-hover disabled:opacity-50";

export function GoogleEngineSetup({ engine, onChanged }: { engine: Engine; onChanged: () => void }) {
  const [operation, setOperation] = useState<GoogleSetupOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [callback, setCallback] = useState("");
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);

  useEffect(() => {
    let cancelled = false;
    api.googleSetupStatus().then((status) => {
      if (!cancelled && status?.phase === "running") setOperation(status);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (operation?.phase !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const status = await api.googleSetupStatus();
        if (cancelled) return;
        setOperation(status);
        if (status?.phase !== "running") {
          changed.current();
          return;
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not check Google setup.");
      }
      if (!cancelled) timer = setTimeout(() => void poll(), 1500);
    };
    timer = setTimeout(() => void poll(), 1000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [operation?.id, operation?.phase]);

  const act = async (work: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await work(); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not complete Google setup."); }
    finally { setBusy(false); }
  };
  const running = operation?.phase === "running";
  const start = (kind: "install" | "sign_in" | "models") => void act(async () => {
    setCallback("");
    setOperation(await api.startGoogleSetup(kind));
  });

  return <div className="space-y-3">
    <div className="flex flex-wrap gap-2">
      {!engine.installed && <button type="button" className={BUTTON} disabled={busy || running} onClick={() => start("install")}>Install Antigravity</button>}
      {engine.installed && <button type="button" className={BUTTON} disabled={busy || running} onClick={() => start("sign_in")}>{engine.signed_in ? "Switch Google account" : "Sign in with Google"}</button>}
      {engine.signed_in && <>
        <button type="button" className={BUTTON} disabled={busy || running} onClick={() => start("models")}>Refresh models</button>
        <button type="button" className={BUTTON} disabled={busy || running} onClick={() => void act(async () => { await api.deleteEngineConfig("antigravity"); setOperation(null); changed.current(); })}>Remove Google account</button>
      </>}
      {running && <button type="button" className={BUTTON} disabled={busy} onClick={() => void act(async () => { await api.cancelGoogleSetup(); setOperation(await api.googleSetupStatus()); setCallback(""); changed.current(); })}>Cancel setup</button>}
    </div>
    {running && <p role="status" className="text-xs text-muted">{operation.kind === "install" ? "Downloading and verifying the official Google runtime. This can take a few minutes." : operation.kind === "models" ? "Refreshing the models available to your account…" : "Waiting for Google sign-in and loading your models…"}</p>}
    {running && operation.authorization_url && <div className="space-y-2">
      <a className="text-sm underline" href={operation.authorization_url} target="_blank" rel="noreferrer">Continue to Google sign-in</a>
      <details><summary className="cursor-pointer text-xs">Signing in from another computer?</summary>
        <p className="mt-2 text-xs text-muted">After signing in, copy the complete localhost redirect URL from your browser and paste it below.</p>
        <form className="mt-2 flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); void act(async () => { const value = callback; setCallback(""); await api.completeGoogleSetup(operation.id, value); }); }}>
          <input aria-label="Google redirect URL" type="password" autoComplete="off" value={callback} onChange={(event) => setCallback(event.target.value)} className="min-w-0 flex-1 rounded-md border border-line bg-transparent px-3 py-2 text-sm" />
          <button className={BUTTON} disabled={busy || !callback.trim()}>Complete sign-in</button>
        </form>
      </details>
    </div>}
    {operation?.phase === "succeeded" && <p role="status" className="text-xs text-success">{operation.kind === "install" ? "Antigravity installed. Sign in with Google to continue." : "Google account models refreshed."}</p>}
    {(error || operation?.error) && <p role="alert" className="text-xs text-danger">{error || operation?.error}</p>}
  </div>;
}
