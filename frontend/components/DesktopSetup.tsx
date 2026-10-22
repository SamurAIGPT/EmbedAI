"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type ApplicationSetting, type ApprovalMode, type DesktopSettings, type Engine } from "@/lib/api";
import { APPROVAL_MODES, modelLabel } from "@/lib/agents";
import { EngineSettings } from "./EngineSettings";
import { EngineModelFields } from "./chat/EngineModelFields";

const BUTTON = "rounded-md border border-line px-3 py-2 text-sm disabled:opacity-50";
const FIELD = "mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm";
const STEPS = ["Engine", "Workspace", "Approvals", "Ready"];

export function DesktopSetup({ settings, onSaved, onFinished }: {
  settings: DesktopSettings;
  onSaved: (setting: ApplicationSetting) => void;
  onFinished: (agentId?: number, selection?: { provider: string; model: string | null }) => void;
}) {
  const row = (key: string) => settings.settings.find((item) => item.key === key);
  const [step, setStep] = useState(0);
  const [engines, setEngines] = useState<Engine[]>([]);
  const [provider, setProvider] = useState("claude");
  const [model, setModel] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState(String(row("LOCAL_WORKSPACE_ROOT")?.value ?? ""));
  const [tools, setTools] = useState(row("LOCAL_TOOLS_ENABLED")?.value === true);
  const [mode, setMode] = useState<ApprovalMode>("ask");
  const [createAgent, setCreateAgent] = useState<boolean | null>(null);
  const [agentName, setAgentName] = useState("Assistant");
  const [createdAgentId, setCreatedAgentId] = useState<number | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const engine = engines.find((item) => item.id === provider);

  useEffect(() => {
    let cancelled = false;
    api.listAgents().then((agents) => {
      if (!cancelled) setCreateAgent((previous) => previous ?? agents.length === 0);
    }).catch(() => { if (!cancelled) setError("Could not load agents. Reload Settings to try again, or set up later."); });
    return () => { cancelled = true; };
  }, []);

  const browse = async () => {
    setError(null); setBusy(true);
    try {
      if (!window.openDots) throw new Error("Folder selection is available in the desktop app.");
      const selected = await window.openDots.choosePath("folder");
      if (selected !== null) setWorkspace(selected);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not choose a folder."); }
    finally { setBusy(false); }
  };

  const complete = async (skip = false) => {
    setBusy(true); setError(null);
    let agentId = createdAgentId;
    try {
      if (!skip) {
        if (!engine?.available) throw new Error("Choose an available engine, or set up later.");
        for (const [key, value] of [["LOCAL_WORKSPACE_ROOT", workspace.trim()], ["LOCAL_TOOLS_ENABLED", tools]] as const) {
          const current = row(key);
          if (!current) throw new Error("This backend does not support setup. Update it and try again.");
          if (value !== current.value) onSaved(await api.saveDesktopSetting(key, value, current.version));
        }
        if (createAgent) {
          const input = { name: agentName.trim(), instructions: "", provider, model: model?.trim() || null, approval_mode: mode };
          const saved = agentId === undefined ? await api.createAgent(input) : await api.updateAgent(agentId, input);
          agentId = saved.id;
          setCreatedAgentId(saved.id);
        }
      }
      const completed = row("DESKTOP_ONBOARDING_COMPLETED");
      if (!completed) throw new Error("This backend does not support setup. Update it and try again.");
      if (completed.value !== true) onSaved(await api.saveDesktopSetting(completed.key, true, completed.version));
      onFinished(skip ? undefined : agentId, skip ? undefined : { provider, model: model?.trim() || null });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Could not finish setup. Your saved changes are kept; retry to continue.");
    } finally { setBusy(false); }
  };

  return <section aria-label="First-run setup" className="space-y-5 rounded-xl border border-line p-5">
    <div><h2 className="text-xl font-semibold">Welcome to Open Duet</h2><p className="mt-2 text-sm text-muted">Choose how your agents work on this computer. You can change these choices in Settings and Agents.</p></div>
    <ol className="flex flex-wrap gap-3 text-xs" aria-label="Setup progress">{STEPS.map((name, index) => <li key={name} aria-current={index === step ? "step" : undefined} className={index === step ? "font-semibold" : "text-muted"}>{index + 1}. {name}</li>)}</ol>
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {step === 0 && <div className="space-y-4">
      <p className="text-sm text-muted">Use a CLI you have signed in to, or configure your own endpoint. For CLI sign-in, run <code>claude auth login</code> or <code>codex login</code> in a terminal, then refresh status below.</p>
      <EngineModelFields engines={engines} provider={provider} model={model} disabled={busy} onChange={(nextProvider, nextModel) => { setProvider(nextProvider); setModel(nextModel); }} />
      <details><summary className="cursor-pointer text-sm underline">Detect and configure engines</summary><div className="mt-3"><EngineSettings onEnginesChanged={setEngines} /></div></details>
      {!engines.length && <p role="status" className="text-sm text-muted">Checking installed engines…</p>}
    </div>}
    {step === 1 && <div className="space-y-3">
      <label className="block text-sm">Default workspace root<input aria-label="Setup workspace root" className={FIELD} value={workspace} onChange={(event) => setWorkspace(event.target.value)} disabled={busy} placeholder="Use the app's own workspace" autoComplete="off" spellCheck={false} /></label>
      <button type="button" className={BUTTON} disabled={busy} onClick={() => void browse()}>Choose folder</button>
      <p className="break-all text-xs text-muted">App default: {String(row("LOCAL_WORKSPACE_ROOT")?.default ?? "")}</p>
      <p className="text-sm text-muted">New agents get their own subfolders here. To work directly in an existing project, set that agent&apos;s workspace in Agents.</p>
    </div>}
    {step === 2 && <div className="space-y-4">
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={tools} disabled={busy} onChange={(event) => setTools(event.target.checked)} />Allow tools on this computer</label>
      <p className="text-xs text-muted">When off, local CLIs stay read only. API endpoints are chat only. Tool-capable engines use their own permissions and the app&apos;s approval cards.</p>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={createAgent === true} disabled={busy || createAgent === null} onChange={(event) => setCreateAgent(event.target.checked)} />Create a starter agent with the selected engine</label>
      {createAgent && <label className="block text-sm">Agent name<input aria-label="Starter agent name" className={FIELD} maxLength={80} value={agentName} disabled={busy} onChange={(event) => setAgentName(event.target.value)} /></label>}
      <fieldset className="space-y-2" disabled={busy || !createAgent}><legend className="mb-2 text-sm font-medium">Starter agent approval mode</legend>{APPROVAL_MODES.map((option) => <label key={option.value} className="flex items-start gap-2 text-sm"><input type="radio" name="setup-approval" value={option.value} checked={mode === option.value} onChange={() => setMode(option.value)} /><span>{option.label}<span className="mt-1 block text-xs text-muted">{option.description}</span></span></label>)}</fieldset>
      {!createAgent && <p className="text-xs text-muted">Your existing agents keep their approval modes. Change them in Agents.</p>}
    </div>}
    {step === 3 && <div className="space-y-3 text-sm"><h3 className="font-semibold">Ready to start</h3><p>{engine?.label ?? provider} · {modelLabel(model)}</p><p className="break-all">Workspace root: {workspace.trim() || "App default"}</p><p>Local tools: {tools ? "on" : "off"}</p><p>{createAgent ? `Create ${agentName.trim()} with ${APPROVAL_MODES.find((item) => item.value === mode)?.label}.` : "Use your existing agents, or start a plain chat."}</p></div>}
    <div className="flex flex-wrap gap-2">
      {step > 0 && <button type="button" className={BUTTON} disabled={busy} onClick={() => { setError(null); setStep((value) => value - 1); }}>Back</button>}
      {step < STEPS.length - 1 ? <button type="button" className={BUTTON} disabled={busy || createAgent === null || (step === 0 && !engine?.available) || (step === 2 && createAgent === true && !agentName.trim())} onClick={() => { setError(null); setStep((value) => value + 1); }}>Continue</button> : <button type="button" className={BUTTON} disabled={busy} onClick={() => void complete()}>{busy ? "Saving…" : "Start chatting"}</button>}
      <button type="button" className={`${BUTTON} ml-auto`} disabled={busy} onClick={() => void complete(true)}>Set up later</button>
    </div>
  </section>;
}
