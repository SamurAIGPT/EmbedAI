import type { Agent, AgentMemory, ApprovalMode } from "./api";

// Suggestions for the model box; any text the Claude CLI accepts also works.
export const MODEL_SUGGESTIONS = ["default", "sonnet", "opus", "haiku"] as const;

export const APPROVAL_MODES: { value: ApprovalMode; label: string; description: string }[] = [
  {
    value: "ask",
    label: "Ask first",
    description: "Your agent asks before it writes a file or runs a command. Reading is allowed.",
  },
  {
    value: "accept_edits",
    label: "Accept edits",
    description: "File edits go ahead without asking. Commands still ask first.",
  },
  {
    value: "plan",
    label: "Plan only",
    description: "Your agent looks around and writes a plan, but changes nothing.",
  },
];

export function approvalModeLabel(mode: string): string {
  return APPROVAL_MODES.find((m) => m.value === mode)?.label ?? mode;
}

// "default" in the box means the provider's own default, which is stored as no model.
export function modelFromInput(text: string): string | null {
  const model = text.trim();
  return model === "" || model.toLowerCase() === "default" ? null : model;
}

export function modelLabel(model: string | null): string {
  return model ?? "default";
}

// Names by id, for lists that show which agent a conversation uses.
export function agentNames(agents: Agent[] | null): Map<number, string> {
  return new Map((agents ?? []).map((agent) => [agent.id, agent.name]));
}

// The agents a person can start a chat with: archived ones are left out.
export function activeAgents(agents: Agent[] | null): Agent[] {
  return (agents ?? []).filter((agent) => agent.archived_at === null);
}

// How full the notes are against what loads each turn: by lines or by bytes,
// whichever is closer to its limit. "near" from 80%, "over" once anything is cut.
export type MemoryGauge = { percent: number; level: "ok" | "near" | "over"; text: string };

export function memoryGauge(memory: Pick<AgentMemory, "lines" | "bytes" | "max_lines" | "max_bytes" | "truncated" | "loaded_lines" | "expired" | "expired_lines">): MemoryGauge {
  const share = Math.max(memory.lines / memory.max_lines, memory.bytes / memory.max_bytes);
  const percent = Math.min(100, Math.round(share * 100));
  const level = memory.truncated ? "over" : share >= 0.8 ? "near" : "ok";
  const size = `${memory.lines} of ${memory.max_lines} lines · ${formatKB(memory.bytes)} of ${formatKB(memory.max_bytes)}`;
  const past = Math.max(0, memory.lines - (memory.expired_lines ?? 0) - memory.loaded_lines);
  const cut = memory.truncated ? ` · ${past} lines past the limit do not load` : "";
  const expired = memory.expired ? ` · ${memory.expired} expired ${memory.expired === 1 ? "note is" : "notes are"} not loaded` : "";
  return { percent, level, text: size + cut + expired };
}

function formatKB(bytes: number): string {
  return bytes < 1000 ? `${bytes} B` : `${(bytes / 1000).toFixed(bytes < 10_000 ? 1 : 0)} KB`;
}

// One line for what a tidy-up did.
export function tidyLine(r: { organized: number; expired: number; duplicates: number; superseded: number; note: string | null }): string {
  const parts = [
    r.organized ? `${r.organized} filed into topic notes` : "",
    r.expired ? `${r.expired} expired moved to the archive` : "",
    r.duplicates ? `${r.duplicates} repeat${r.duplicates === 1 ? "" : "s"} removed` : "",
    r.superseded ? `${r.superseded} contradicted note${r.superseded === 1 ? "" : "s"} struck through` : "",
  ].filter(Boolean);
  return (parts.length ? `Tidied: ${parts.join(", ")}.` : "Nothing to tidy.") + (r.note ? ` ${r.note}` : "");
}

// The text of an agent file as saved to disk.
export function agentFileText(document: unknown): string {
  return JSON.stringify(document, null, 2) + "\n";
}

// An agent file read back from disk. Throws an Error with a sentence a person
// can act on; the backend checks everything else.
export function parseAgentFile(text: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error("That file is not an agent file (it is not JSON).");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("That file is not an agent file.");
  }
  return value as Record<string, unknown>;
}

// Whether a parsed agent file carries notes, so the import can offer to skip them.
export function agentFileHasNotes(document: Record<string, unknown>): boolean {
  const notes = document.notes as Record<string, unknown> | undefined;
  return typeof notes?.["MEMORY.md"] === "string" && (notes["MEMORY.md"] as string).trim() !== "";
}

// After an export: which parts had something that looked like a secret replaced.
export function redactionNotice(parts: string[]): string | null {
  if (parts.length === 0) return null;
  return `Something that looked like a secret was replaced in: ${parts.join(", ")}.`;
}
