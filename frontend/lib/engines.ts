import type { Engine, EngineConfigInput } from "./api";

export function engineGroups(engines: Engine[]): { label: string; engines: Engine[] }[] {
  return [
    { label: "Ready", engines: engines.filter((engine) => engine.available) },
    { label: "Needs setup", engines: engines.filter((engine) => !engine.available) },
  ].filter((group) => group.engines.length > 0);
}

const FIRST_RUN_ORDER = ["claude", "codex", "cursor", "opencode", "antigravity", "gemini", "openrouter"];

export function firstReadyEngine(engines: Engine[], current?: string): string | null {
  if (current && engines.some((engine) => engine.id === current && engine.available)) return current;
  return FIRST_RUN_ORDER.find((id) => engines.some((engine) => engine.id === id && engine.available))
    ?? engines.find((engine) => engine.available)?.id ?? null;
}

export function engineDraft(engine: Engine): Record<string, string> {
  return Object.fromEntries((engine.config_fields ?? []).map((field) => [
    field.name, field.kind === "secret" ? "" : String(engine.config?.[field.name] ?? ""),
  ]));
}

export function engineConfig(engine: Engine, draft: Record<string, string>): EngineConfigInput {
  return Object.fromEntries((engine.config_fields ?? [])
    .map((field) => [field.name, (draft[field.name] ?? "").trim()] as const)
    .filter(([, value]) => value !== ""));
}

export const ENDPOINT_TEMPLATES = [
  { label: "Ollama", url: "http://127.0.0.1:11434/v1" },
  { label: "LM Studio", url: "http://127.0.0.1:1234/v1" },
  { label: "Groq", url: "https://api.groq.com/openai/v1" },
  { label: "Cerebras", url: "https://api.cerebras.ai/v1" },
  { label: "Mistral", url: "https://api.mistral.ai/v1" },
  { label: "OpenAI", url: "https://api.openai.com/v1" },
];
