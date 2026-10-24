"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, isLaunchTokenMissing, type ApplicationSetting, type DesktopSettings } from "@/lib/api";
import { ApprovalsTabs } from "@/components/approvals/ApprovalsPanel";
import { EngineSettings } from "@/components/EngineSettings";
import { DesktopSetup } from "@/components/DesktopSetup";
import { DevicesSection } from "@/components/DevicesSection";
import { AppUpdates } from "@/components/AppUpdates";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

declare global {
  interface Window {
    openDots?: {
      choosePath(kind: "file" | "folder"): Promise<string | null>;
      nativeControl(request: import("@/components/chat/NativeComputer").NativeRequest): Promise<import("@/components/chat/NativeComputer").NativeStatus | { held: boolean }>;
      updates?: import("@/components/AppUpdates").UpdatesBridge;
    };
  }
}

const fields: Record<string, { label: string; help: string; type: "file" | "folder" | "checkbox" | "number"; min?: number; max?: number }> = {
  LOCAL_CLAUDE_CLI_PATH: { label: "Claude executable", help: "Leave empty for automatic detection from PATH. A selected executable runs as your account. Windows npm claude.cmd shims are supported.", type: "file" },
  LOCAL_WORKSPACE_ROOT: { label: "Workspace root", help: "New conversations use a workspace subfolder here. Existing conversations keep their original folder. Leave empty for the app default.", type: "folder" },
  LOCAL_TOOLS_ENABLED: { label: "Allow local tools", help: "Allows file reads, file changes and commands on this computer. Calls Claude asks about still require Allow or Deny; your existing Claude permission rules also apply. When off, Claude can only chat.", type: "checkbox" },
  LOCAL_TASK_TIMEOUT_SECONDS: { label: "Answer timeout (seconds)", help: "Waiting for approval pauses this timer.", type: "number", min: 30, max: 3600 },
  LOCAL_APPROVAL_TIMEOUT_SECONDS: { label: "Approval timeout (seconds)", help: "New approvals use this limit. Questions already waiting keep their deadline. Unanswered questions are denied.", type: "number", min: 30, max: 3600 },
  LOCAL_MAX_OUTPUT_BYTES: { label: "Answer output limit (bytes)", help: "Bounds the answer text retained from each run.", type: "number", min: 1024, max: 16000000 },
};

const inputClass = "block w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm disabled:opacity-50";
const buttonClass = "cursor-pointer rounded-md border border-line px-3 py-2 text-sm hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const display = (value: unknown) => value === null || value === undefined ? "Unavailable" : value === "" ? "Automatic / app default" : typeof value === "string" ? value : JSON.stringify(value);
const applies = (row: ApplicationSetting) => row.key === "LOCAL_WORKSPACE_ROOT" ? "New conversations" : row.key === "LOCAL_APPROVAL_TIMEOUT_SECONDS" ? "New approvals" : row.applies === "next_run" ? "Next turn" : row.applies === "restart" ? "After restart" : "Immediately";

export function SettingsApp() {
  const router = useRouter();
  const [data, setData] = useState<DesktopSettings | null>(null);
  const [drafts, setDrafts] = useState<Record<string, unknown>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [loadTick, setLoadTick] = useState(0);
  const [checking, setChecking] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [headless, setHeadless] = useState(false);
  const [setupReplay, setSetupReplay] = useState(false);
  const settingUp = setupReplay || data?.settings.some((row) => row.key === "DESKTOP_ONBOARDING_COMPLETED" && row.value === false) === true;

  useEffect(() => {
    let cancelled = false;
    api.getDesktopSettings().then((value) => {
      if (cancelled) return;
      setData(value);
      setHeadless(false);
      setDrafts(Object.fromEntries(value.settings.map((row) => [row.key, row.value])));
      setError(null);
      setNotice(null);
    }).catch((err: unknown) => {
      if (cancelled) return;
      if (err instanceof ApiError && err.status === 404) { setHeadless(true); setError(null); }
      else setError(isLaunchTokenMissing(err) ? "Open Settings inside the Open Duet app window." : err instanceof ApiError ? err.message : "Could not reach the backend. Check that the app is running.");
    });
    return () => { cancelled = true; };
  }, [loadTick]);

  const save = async (row: ApplicationSetting, event: React.FormEvent) => {
    event.preventDefault();
    setBusy(row.key); setError(null); setNotice(null);
    try {
      const saved = await api.saveDesktopSetting(row.key, drafts[row.key], row.version);
      setData((previous) => previous && ({ ...previous, settings: previous.settings.map((item) => item.key === row.key ? saved : item) }));
      setDrafts((previous) => ({ ...previous, [row.key]: saved.value }));
      setNotice(`${fields[row.key].label} saved. Applies to ${applies(saved).toLowerCase()}. Running turns and existing approval deadlines stay unchanged.`);
      if (row.key === "LOCAL_CLAUDE_CLI_PATH") setStatus(null);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not save. Your changes remain in the form."); }
    finally { setBusy(null); }
  };

  const browse = async (row: ApplicationSetting) => {
    setBusy(row.key); setError(null);
    try {
      if (!window.openDots) throw new Error("Browse is available inside the desktop app.");
      const selected = await window.openDots.choosePath(fields[row.key].type === "file" ? "file" : "folder");
      if (selected !== null) setDrafts((previous) => ({ ...previous, [row.key]: selected }));
    } catch (err) { setError(err instanceof Error ? err.message : "Could not open the picker."); }
    finally { setBusy(null); }
  };

  const check = async () => {
    setChecking(true);
    try {
      const result = await api.testDesktopClaude();
      setStatus(result.available ? `Ready${result.version ? ` · ${result.version}` : ""}` : result.error || "Claude is not ready.");
    } catch { setStatus("Could not check Claude. Try again."); }
    finally { setChecking(false); }
  };

  return <div className="min-h-dvh">
    <header className="flex h-12 items-center gap-4 border-b border-line px-4"><Link href="/chat/" className="text-sm text-muted hover:underline">← Chat</Link><h1 className="text-sm font-semibold">Settings</h1></header>
    <main className="mx-auto max-w-2xl space-y-6 px-5 py-8">
      <div><h2 className="text-xl font-semibold">Settings</h2><p className="mt-2 text-sm text-muted">Configure engines and approvals. Desktop preferences can be saved below when using the app.</p></div>
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-card border border-line bg-raised p-5">
        <div><h3 className="font-semibold">Appearance</h3><p className="mt-1 text-xs text-muted">Follows your system by default. Saved in this browser only.</p></div>
        <ThemeToggle />
      </section>
      <AppUpdates />
      {error && <p role="alert" className="rounded-md border border-danger/50 p-3 text-sm text-danger">{error}</p>}
      {notice && <p role="status" className="rounded-md border border-line p-3 text-sm">{notice}</p>}
      {headless && <p className="text-sm text-muted">Workspace, executable and timeout preferences use the backend environment configuration in this installation.</p>}
      {!data && !error && !headless && <p role="status">Loading settings…</p>}
      {!headless && <button className={buttonClass} disabled={busy !== null} onClick={() => setLoadTick((n) => n + 1)}>{data ? "Reload saved settings" : "Try again"}</button>}
      {data && settingUp && <DesktopSetup settings={data} onSaved={(saved) => {
        setData((previous) => previous && ({ ...previous, settings: previous.settings.map((item) => item.key === saved.key ? saved : item) }));
        setDrafts((previous) => ({ ...previous, [saved.key]: saved.value }));
      }} onFinished={(agentId, selection) => {
        setSetupReplay(false);
        const query = new URLSearchParams();
        if (agentId !== undefined) query.set("agent", String(agentId));
        else if (selection) { query.set("engine", selection.provider); if (selection.model) query.set("model", selection.model); }
        router.push(`/chat/${query.size ? `?${query}` : ""}`);
      }} />}
      {data && !settingUp && <>
        {data.settings.some((row) => row.key === "DESKTOP_ONBOARDING_COMPLETED") && <button type="button" className={buttonClass} onClick={() => setSetupReplay(true)}>Run setup again</button>}
        <section className="space-y-5 rounded-lg border border-line p-5">
          <h3 className="font-semibold">Claude Code</h3>
          {data.settings.map((row) => {
            const field = fields[row.key];
            if (!field) return null;
            const value = drafts[row.key];
            const dirty = JSON.stringify(value) !== JSON.stringify(row.value);
            return <form key={row.key} onSubmit={(event) => save(row, event)} className="space-y-2 border-b border-line pb-5 last:border-0 last:pb-0">
              <label htmlFor={row.key} className="block text-sm font-medium">{field.label}</label>
              <div className="flex items-center gap-2">
                {field.type === "checkbox" ? <input id={row.key} type="checkbox" disabled={busy !== null} checked={value === true} onChange={(event) => setDrafts((previous) => ({ ...previous, [row.key]: event.target.checked }))} /> : <input id={row.key} className={inputClass} disabled={busy !== null} type={field.type === "number" ? "number" : "text"} min={field.min} max={field.max} step={1} required={field.type === "number"} value={field.type === "number" ? typeof value === "number" ? value : "" : typeof value === "string" ? value : ""} onChange={(event) => setDrafts((previous) => ({ ...previous, [row.key]: field.type === "number" ? event.target.value === "" ? null : Number(event.target.value) : event.target.value }))} autoComplete="off" spellCheck={false} />}
                {(field.type === "file" || field.type === "folder") && <button type="button" aria-label={`Browse ${field.label}`} disabled={busy !== null} className={buttonClass} onClick={() => browse(row)}>Browse</button>}
              </div>
              <p className="text-xs text-muted">{field.help}{field.type === "number" ? ` Range: ${field.min}–${field.max}.` : ""}</p>
              <div className="flex flex-wrap gap-2 text-xs text-muted"><span className="rounded bg-inset px-2 py-1">Source: {row.source}</span><span className="rounded bg-inset px-2 py-1">{applies(row)}</span></div>
              <p className="break-all text-xs text-muted">Effective: {display(row.effective_value)} · Default: {display(row.default)}</p>
              {row.resolved_path && <p className="break-all font-mono text-xs text-muted">New conversation folder: {row.resolved_path}</p>}
              {row.error && <p role="alert" className="text-xs text-danger">{row.error} Stored value: {display(row.value)}</p>}
              <div className="flex gap-2"><button type="submit" disabled={busy !== null || !dirty} className={buttonClass} aria-label={`Save ${field.label}`}>{busy === row.key ? "Working…" : "Save"}</button><button type="button" className={buttonClass} disabled={busy !== null || !dirty} onClick={() => setDrafts((previous) => ({ ...previous, [row.key]: row.value }))}>Discard</button></div>
            </form>;
          })}
          <button className={buttonClass} disabled={checking || busy !== null} onClick={check}>{checking ? "Checking…" : "Check Claude status"}</button>
          {status && <p role="status" className="text-sm">{status}</p>}
          <p className="text-xs text-muted">Checks saved settings. To sign in, run <code>claude auth login</code> in your terminal, then check again.</p>
        </section>
        <section className="space-y-4 rounded-lg border border-line p-5"><h3 className="font-semibold">Storage</h3><p className="text-xs text-muted">Managed by the desktop app. These locations cannot be changed here.</p><dl className="space-y-3 text-sm">{Object.entries(data.storage).map(([name, value]) => <div key={name}><dt className="font-medium capitalize">{name.replaceAll("_", " ")}</dt><dd className="mt-1 break-all text-xs text-muted">{value}</dd></div>)}</dl></section>
      </>}
      {!settingUp && <>
        <DevicesSection />
        <EngineSettings />
        <section className="rounded-lg border border-line p-5"><h3 className="mb-1 font-semibold">Approvals</h3><p className="mb-4 text-xs text-muted">Commands you always allow, every decision about a tool call, and a read-only health scan of recent conversations.</p><ApprovalsTabs /></section>
      </>}
    </main>
  </div>;
}
