"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type Engine, type EngineConfigField } from "@/lib/api";
import { ENDPOINT_TEMPLATES, engineConfig, engineDraft, engineGroups } from "@/lib/engines";
import { GoogleEngineSetup } from "./GoogleEngineSetup";

const FIELD = "mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm";
const BUTTON = "cursor-pointer rounded-md border border-line px-3 py-2 text-sm hover:bg-hover disabled:opacity-50";

export function EngineSettings({ onEnginesChanged }: { onEnginesChanged?: (engines: Engine[]) => void } = {}) {
  const [engines, setEngines] = useState<Engine[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});
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
      setDrafts((previous) => ({ ...Object.fromEntries(items.map((item) => [item.id, engineDraft(item)])), ...previous }));
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
      const saved = await api.saveEngineConfig(engine.id, engineConfig(engine, draft));
      const updated = engines?.map((item) => item.id === engine.id ? saved : item) ?? [];
      setEngines(updated);
      onEnginesChanged?.(updated);
      setDrafts((previous) => ({ ...previous, [engine.id]: engineDraft(saved) }));
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

  const field = (engine: Engine, descriptor: EngineConfigField) => {
    const hasSecret = descriptor.kind === "secret" && engine.config?.[`has_${descriptor.name}`] === true;
    return <label key={descriptor.name} className="block min-w-0 text-xs text-muted">{descriptor.label}{hasSecret ? " (saved; leave empty to keep)" : descriptor.required ? "" : " (optional)"}
      {engine.id === "antigravity" && descriptor.name === "model" ? <select className={FIELD} value={drafts[engine.id]?.model ?? ""}
        onChange={(event) => setDrafts((old) => ({ ...old, [engine.id]: { ...old[engine.id], model: event.target.value } }))}>
        <option value="">Account default</option>{engine.models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
      </select> : <input className={FIELD} type={descriptor.kind === "secret" ? "password" : descriptor.kind === "url" ? "url" : "text"}
        required={descriptor.required && !hasSecret} value={drafts[engine.id]?.[descriptor.name] ?? ""}
        placeholder={descriptor.placeholder ?? undefined} autoComplete={descriptor.kind === "secret" ? "new-password" : "off"}
        onChange={(event) => setDrafts((old) => ({ ...old, [engine.id]: { ...old[engine.id], [descriptor.name]: event.target.value } }))} />}
      {descriptor.help && <span className="mt-1 block">{descriptor.help}</span>}
    </label>;
  };

  return <section className="space-y-4 rounded-lg border border-line p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-semibold">Engines</h3><p className="mt-1 text-xs text-muted">Connect an AI, then choose it for an agent or conversation.</p></div><button className={BUTTON} disabled={busy !== null} onClick={() => { setBusy("refresh"); setRefresh((n) => n + 1); }}>{busy === "refresh" ? "Checking…" : "Check again"}</button></div>
    {error && <p role="alert" className="rounded-md border border-danger/50 p-3 text-sm text-danger">{error}</p>}
    {notice && <p role="status" className="rounded-md border border-line p-3 text-sm">{notice}</p>}
    {!engines && !error && <p role="status" className="text-sm text-muted">Checking engines…</p>}
    {engineGroups(engines ?? []).map((group) => <div key={group.label} className="space-y-3"><h4 className="text-sm font-semibold">{group.label}</h4>{group.engines.map((engine) => {
      const fields = engine.config_fields ?? [];
      return <article key={engine.id} className="min-w-0 space-y-3 rounded-md border border-line p-4">
        <div className="flex flex-wrap items-center gap-2"><h5 className="font-medium">{engine.label}</h5><span className={`rounded px-2 py-0.5 text-xs ${engine.available ? "bg-success/15 text-success" : "bg-warning/15 text-warning"}`}>{engine.available ? "Ready" : "Needs setup"}</span>{engine.version && <span className="text-xs text-muted">{engine.version}</span>}</div>
        {!engine.available && <p className="break-words text-xs text-muted">{engine.reason}</p>}
        {engine.id === "antigravity" && <GoogleEngineSetup engine={engine} onChanged={() => {
          setDrafts((previous) => { const next = { ...previous }; delete next.antigravity; return next; });
          setRefresh((n) => n + 1);
        }} />}
        {engine.setup && <div className="space-y-1 text-xs text-muted">
          {engine.setup.install && <p>Install: <code className="break-all">{engine.setup.install}</code></p>}
          {engine.setup.sign_in && <p>Sign in: <code className="break-all">{engine.setup.sign_in}</code></p>}
          {engine.setup.notes?.map((note) => <p key={note}>{note}</p>)}
          {engine.setup.docs_url && <a className="inline-block underline" href={engine.setup.docs_url} target="_blank" rel="noreferrer">Connection guide</a>}
        </div>}
        <p className="text-xs text-muted">{engine.models.length} model suggestions{engine.custom_models ? " · custom model IDs accepted" : ""}</p>
        {engine.configurable && fields.length > 0 && <form onSubmit={(event) => { event.preventDefault(); void save(engine); }}><fieldset disabled={busy !== null} className="space-y-3">
          {fields.some((item) => item.name === "base_url") && <label className="block text-xs text-muted">Fill address from template<select className={FIELD} value="" onChange={(event) => setDrafts((old) => ({ ...old, [engine.id]: { ...old[engine.id], base_url: event.target.value } }))}><option value="">Choose a service…</option>{ENDPOINT_TEMPLATES.map((template) => <option key={template.label} value={template.url}>{template.label}</option>)}</select></label>}
          <div className="grid gap-3 sm:grid-cols-2">{fields.filter((item) => !item.advanced).map((item) => field(engine, item))}</div>
          {fields.some((item) => item.advanced) && <details><summary className="cursor-pointer text-sm">Advanced</summary><div className="mt-3 grid gap-3 sm:grid-cols-2">{fields.filter((item) => item.advanced).map((item) => field(engine, item))}</div></details>}
          <div className="flex flex-wrap gap-2"><button type="submit" className={BUTTON}>{busy === engine.id ? "Saving…" : "Save engine"}</button>{engine.config && engine.id !== "antigravity" && <button type="button" className={BUTTON} onClick={() => void forget(engine)}>Forget settings</button>}</div>
        </fieldset></form>}
      </article>;
    })}</div>)}
  </section>;
}
