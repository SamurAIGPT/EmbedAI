"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type Engine } from "@/lib/api";

const FIELD = "w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm";
const BUTTON = "cursor-pointer rounded-md border border-line px-3 py-2 text-sm hover:bg-hover disabled:opacity-50";

export function EngineSettings({ onEnginesChanged }: { onEnginesChanged?: (engines: Engine[]) => void } = {}) {
  const [engines, setEngines] = useState<Engine[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, { command: string; label: string; base_url: string; model: string; api_key: string }>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>("refresh");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let cancelled = false;
    api.getEngines(refresh > 0).then((items) => {
      if (cancelled) return;
      setEngines(items);
      onEnginesChanged?.(items);
      setDrafts((previous) => ({ ...Object.fromEntries(items.map((item) => [item.id, {
        command: typeof item.config?.command === "string" ? item.config.command : "",
        label: typeof item.config?.label === "string" ? item.config.label : "",
        base_url: typeof item.config?.base_url === "string" ? item.config.base_url : "",
        model: typeof item.config?.model === "string" ? item.config.model : "",
        api_key: "",
      }])), ...previous }));
      setError(null);
    }).catch((err: unknown) => {
      if (!cancelled) setError(err instanceof ApiError ? err.message : "Could not load engines. Check that the backend is running.");
    }).finally(() => { if (!cancelled) setBusy(null); });
    return () => { cancelled = true; };
  }, [refresh, onEnginesChanged]);

  const save = async (engine: Engine) => {
    const draft = drafts[engine.id];
    if (!draft) return;
    setBusy(engine.id); setError(null); setNotice(null);
    try {
      const config = engine.id === "acp"
        ? { command: draft.command.trim(), label: draft.label.trim() || undefined }
        : { base_url: draft.base_url.trim(), model: draft.model.trim() || undefined, api_key: draft.api_key || undefined };
      const saved = await api.saveEngineConfig(engine.id, config);
      const updated = engines?.map((item) => item.id === engine.id ? saved : item) ?? [];
      setEngines(updated);
      onEnginesChanged?.(updated);
      setDrafts((previous) => ({ ...previous, [engine.id]: { ...previous[engine.id], api_key: "" } }));
      setNotice(`${engine.label} settings saved. ${saved.available ? "Ready to use." : saved.reason ?? "The engine is not ready yet."}`);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not save engine settings."); }
    finally { setBusy(null); }
  };

  const forget = async (engine: Engine) => {
    setBusy(engine.id); setError(null); setNotice(null);
    try {
      await api.deleteEngineConfig(engine.id);
      setDrafts((previous) => {
        const next = { ...previous };
        delete next[engine.id];
        return next;
      });
      setRefresh((n) => n + 1);
      setNotice(`${engine.label} settings and saved credential removed.`);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not remove engine settings."); }
    finally { setBusy(null); }
  };

  return <section className="space-y-4 rounded-lg border border-line p-5">
    <div className="flex items-center justify-between gap-3"><div><h3 className="font-semibold">Engines</h3><p className="mt-1 text-xs text-muted">Choose an installed engine on each agent, or configure an ACP command or OpenAI-compatible endpoint.</p></div><button className={BUTTON} disabled={busy !== null} onClick={() => { setBusy("refresh"); setRefresh((n) => n + 1); }}>{busy === "refresh" ? "Checking…" : "Refresh status"}</button></div>
    {error && <p role="alert" className="rounded-md border border-danger/50 p-3 text-sm text-danger">{error}</p>}
    {notice && <p role="status" className="rounded-md border border-line p-3 text-sm">{notice}</p>}
    {!engines && !error && <p role="status" className="text-sm text-muted">Checking engines…</p>}
    {engines?.map((engine) => {
      const draft = drafts[engine.id];
      const editable = engine.id === "acp" || engine.id === "openai_compat";
      return <article key={engine.id} className="space-y-3 rounded-md border border-line p-4">
        <div className="flex flex-wrap items-center gap-2"><h4 className="font-medium">{engine.label}</h4><span className={`rounded px-2 py-0.5 text-xs ${engine.available ? "bg-success/15 text-success" : "bg-warning/15 text-warning"}`}>{engine.available ? "Ready" : "Unavailable"}</span>{engine.version && <span className="text-xs text-muted">{engine.version}</span>}</div>
        {!engine.available && <p className="text-xs text-muted">{engine.reason}</p>}
        <p className="text-xs text-muted">Models: {engine.models.length ? engine.models.map((model) => model.label + (model.default ? " (default)" : "")).join(", ") : "Use the engine's default"}{engine.custom_models ? " · custom model IDs are accepted" : ""}</p>
        {editable && draft && <form onSubmit={(event) => { event.preventDefault(); void save(engine); }}><fieldset disabled={busy !== null} className="space-y-3">{engine.id === "acp" ? <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">ACP command<input className={`${FIELD} mt-1`} value={draft.command} required maxLength={1000} placeholder="agent-cli --acp" onChange={(e) => setDrafts((old) => ({ ...old, acp: { ...old.acp, command: e.target.value } }))} autoComplete="off" /></label>
          <label className="text-xs text-muted">Display name<input className={`${FIELD} mt-1`} value={draft.label} placeholder="ACP agent" onChange={(e) => setDrafts((old) => ({ ...old, acp: { ...old.acp, label: e.target.value } }))} /></label>
        </div> : <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">Base URL<input className={`${FIELD} mt-1`} type="url" required maxLength={500} value={draft.base_url} placeholder="https://api.example.com/v1" onChange={(e) => setDrafts((old) => ({ ...old, openai_compat: { ...old.openai_compat, base_url: e.target.value } }))} autoComplete="url" /></label>
          <label className="text-xs text-muted">Default model (optional)<input className={`${FIELD} mt-1`} value={draft.model} placeholder="Use endpoint default" onChange={(e) => setDrafts((old) => ({ ...old, openai_compat: { ...old.openai_compat, model: e.target.value } }))} /></label>
          <label className="text-xs text-muted sm:col-span-2">API key{typeof engine.config?.has_api_key === "boolean" && engine.config.has_api_key ? " (saved; leave empty to keep it)" : ""}<input className={`${FIELD} mt-1`} type="password" value={draft.api_key} onChange={(e) => setDrafts((old) => ({ ...old, openai_compat: { ...old.openai_compat, api_key: e.target.value } }))} autoComplete="new-password" /></label>
        </div>}
        <div className="flex gap-2"><button type="submit" className={BUTTON}>{busy === engine.id ? "Saving…" : "Save engine"}</button>{engine.config && <button type="button" className={BUTTON} onClick={() => void forget(engine)}>Forget settings</button>}</div>
        {engine.id === "acp" && <p className="text-xs text-muted">Sign in to this command in a terminal first. It runs its own configured model and asks for tools through ACP.</p>}
        {engine.id === "openai_compat" && <p className="text-xs text-muted">The key is encrypted at rest. Changing the address drops the saved key unless you enter it again.</p>}
        </fieldset></form>}
        {engine.id === "claude" && <p className="text-xs text-muted">Install and sign in to Claude Code in a terminal, then refresh status. The desktop settings above can override its executable.</p>}
        {engine.id === "codex" && <p className="text-xs text-muted">Install and sign in to Codex in a terminal, then refresh status. It uses the executable on your PATH.</p>}
      </article>;
    })}
  </section>;
}
