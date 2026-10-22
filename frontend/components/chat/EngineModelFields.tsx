"use client";

import { useId } from "react";
import type { Engine } from "@/lib/api";
import { engineLimitText } from "@/lib/everyday";

const FIELD = "mt-1 w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm disabled:opacity-50";

export function EngineModelFields({ engines, provider, model, onChange, disabled = false, lockProvider = false, hasComputer = false, defaultLabel }: {
  engines: Engine[];
  provider: string;
  model: string | null;
  onChange: (provider: string, model: string | null) => void;
  disabled?: boolean;
  lockProvider?: boolean;
  hasComputer?: boolean;
  defaultLabel?: string;
}) {
  const listId = useId();
  const engine = engines.find((item) => item.id === provider);
  const knownModel = model === null || engine?.models.some((item) => item.id === model);
  return <div className="space-y-3">
    <label className="block text-xs text-muted">Engine
      <select className={FIELD} aria-label="Engine" value={provider} disabled={disabled || lockProvider} onChange={(event) => onChange(event.target.value, null)}>
        {!engine && <option value={provider}>{provider}</option>}
        {engines.map((item) => <option key={item.id} value={item.id} disabled={hasComputer && !item.capabilities.computers}>{item.label}{item.available ? "" : " (unavailable)"}{item.limit ? ` · ${engineLimitText(item.limit)}` : ""}{hasComputer && !item.capabilities.computers ? " · cannot use this computer" : ""}</option>)}
      </select>
    </label>
    {engine?.limit && <p role="status" className="text-xs text-warning"><span className="mr-1 rounded-full border border-warning/60 px-2 py-0.5 font-medium">{engineLimitText(engine.limit)}</span>{engine.limit.message}</p>}
    {engine && !engine.available && <p className="text-xs text-warning">{engine.reason} <a href="/settings/" className="underline">Open engine settings</a></p>}
    <label className="block text-xs text-muted">Model
      {engine && !engine.custom_models ? <select className={FIELD} aria-label="Model" value={model ?? ""} disabled={disabled} onChange={(event) => onChange(provider, event.target.value || null)}>
        <option value="">{defaultLabel ?? `Engine default${engine.default_model ? ` (${engine.default_model})` : ""}`}</option>
        {!knownModel && model && <option value={model}>{model} (saved; unavailable)</option>}
        {engine.models.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select> : <>
        <input className={FIELD} aria-label="Model" list={listId} value={model ?? ""} disabled={disabled} placeholder={defaultLabel ?? (engine?.default_model ? `Engine default (${engine.default_model})` : "Engine default")} maxLength={200} autoComplete="off" spellCheck={false} onChange={(event) => onChange(provider, event.target.value || null)} />
        <datalist id={listId}>{engine?.models.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</datalist>
      </>}
      <span className="mt-1 block">Leave the model unset to use {defaultLabel ? "the agent's default" : "the engine's default"}.{engine?.custom_models ? " Choose a suggestion or enter a model ID." : ""}</span>
    </label>
    {engine && <p className="text-xs text-muted">{engine.capabilities.tools ? "Tool calls supported" : "Chat only; no tool calls"}{engine.capabilities.computers ? " · agent computers supported" : ""}{engine.capabilities.resume ? " · resumable sessions" : ""}</p>}
  </div>;
}
