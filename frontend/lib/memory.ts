// Topic notes, recall chips, compaction markers, the journal and proposed
// notes (docs/MEMORY.md). Pure: no React, no network.
import type { CaptureReport, MemoryJournalRow, MemoryUpkeepStatus } from "./api";

// A topic is one file name: letters, digits, dashes and underscores, then ".md".
const TOPIC_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.md$/;

export function isTopicName(name: string): boolean {
  return TOPIC_NAME.test(name) && name.toLowerCase() !== "memory.md";
}

// What a person types ("Asha's school") as a topic file name ("asha-s-school.md");
// null when nothing usable is left.
export function topicFileName(raw: string): string | null {
  const base = raw
    .trim()
    .replace(/\.md$/i, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 59)
    .replace(/-+$/, "");
  const name = `${base}.md`;
  return base && isTopicName(name) ? name : null;
}

export type RecalledNote = { name: string; title: string | null; snippet: string };
export type RecalledPassage = { thread_id: number; title: string | null; role: string; at: string | null; snippet: string };

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

// The chip's one line: what was put in front of the message.
export function recalledLabel(notes: number, conversations: number): string {
  const parts = [];
  if (notes) parts.push(plural(notes, "note", "notes"));
  if (conversations) parts.push(plural(conversations, "passage from earlier conversations", "passages from earlier conversations"));
  return `Recalled ${parts.join(" and ") || "nothing"}`;
}

// The marker's one line.
export function compactedLabel(folded: number, writtenBy?: string): string {
  const base =
    folded > 0
      ? `Earlier messages summarized: ${plural(folded, "message was", "messages were")} folded into a summary for the engine. The full conversation stays here.`
      : "Earlier messages summarized for the engine. The full conversation stays here.";
  // A helper model wrote it: the conversation went to that engine, not the agent's own.
  return writtenBy ? `${base} Written by the helper model ${writtenBy}.` : base;
}

const ACTORS: Record<string, string> = { person: "You", proposal: "Accepted proposal", undo: "Undo", upkeep: "Memory upkeep", agent: "Agent", import: "Imported agent file" };
const KINDS: Record<string, string> = { created: "created", edited: "edited", deleted: "deleted" };
const VIA: Record<string, string> = { tidy: "tidy-up", organize: "organizing", capture: "noticing" };

export function journalLabel(row: Pick<MemoryJournalRow, "actor" | "kind" | "path" | "added" | "removed"> & { via?: string | null }): string {
  const step = row.actor === "upkeep" && row.via && VIA[row.via] ? ` (${VIA[row.via]})` : "";
  const who = (ACTORS[row.actor] ?? row.actor) + step;
  const counts = row.kind === "edited" ? ` (+${row.added} −${row.removed})` : "";
  return `${who} ${KINDS[row.kind] ?? row.kind} ${row.path}${counts}`;
}

// The hour the nightly tidy-up may start after, as the person reads it.
export function tidyHourLabel(hour: number): string {
  if (hour >= 24) return "never";
  const h = hour % 12 === 0 ? 12 : hour % 12;
  return `${h}:00 ${hour < 12 ? "am" : "pm"}`;
}

// What the last capture noticed, or "" when it noticed nothing.
export function captureLine(r: CaptureReport | null | undefined): string {
  if (!r) return "";
  const parts = [
    r.added ? `${r.added} for the notes` : "",
    r.topics ? `${r.topics} for topic notes` : "",
    r.about_me ? `${r.about_me} for About me` : "",
  ].filter(Boolean);
  if (parts.length) return `Last noticed: ${parts.join(", ")}.`;
  return r.note ?? "";
}

// Which model the quick calls use, and why a different one might be wanted.
export function helperModelLine(
  status: Pick<MemoryUpkeepStatus, "engine" | "helper_model" | "engine_can_call" | "helper" | "helper_used"> | null,
  allowed = true,
): string {
  if (!status) return "";
  if (!status.engine_can_call) {
    const helper = status.helper;
    if (helper && helper.usable && allowed) {
      return `This agent's engine cannot make the quick call, so the helper model (${helper.label}${helper.model ? `, ${helper.model}` : ""}) writes it: the text read goes to ${helper.label}, not to this agent's provider.`;
    }
    if (helper && !allowed) return "This agent's engine cannot make the quick call and it never uses the helper model, so only expired notes and repeats are tidied.";
    if (helper) return `This agent's engine cannot make the quick call and the helper model (${helper.label}) is not usable, so only expired notes and repeats are tidied.`;
    return "This agent's engine cannot make the quick call, so only expired notes and repeats are tidied.";
  }
  const engine = status.engine ?? "its engine";
  return status.helper_model
    ? `Quick calls use ${status.helper_model} on ${engine}.`
    : `Quick calls use this agent's own model on ${engine}.`;
}

// The settings change that sets (or, with an empty engine, removes) the helper model.
export function helperChange(engine: string, model: string): { helper: { engine: string; model: string } | null } {
  const e = engine.trim();
  return { helper: e ? { engine: e, model: model.trim() } : null };
}

// The settings with one engine's helper model set (an empty name clears it).
export function withHelperModel(models: Record<string, string>, engine: string, model: string): Record<string, string> {
  const next = { ...models };
  const name = model.trim();
  if (name) next[engine] = name;
  else delete next[engine];
  return next;
}
