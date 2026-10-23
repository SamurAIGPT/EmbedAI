// Topic notes, recall chips, compaction markers, the journal and proposed
// notes (docs/MEMORY.md). Pure: no React, no network.
import type { MemoryJournalRow } from "./api";

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
export function compactedLabel(folded: number): string {
  return folded > 0
    ? `Earlier messages summarized: ${plural(folded, "message was", "messages were")} folded into a summary for the engine. The full conversation stays here.`
    : "Earlier messages summarized for the engine. The full conversation stays here.";
}

const ACTORS: Record<string, string> = { person: "You", proposal: "Accepted proposal", undo: "Undo", upkeep: "Memory upkeep", agent: "Agent", import: "Imported agent file" };
const KINDS: Record<string, string> = { created: "created", edited: "edited", deleted: "deleted" };

export function journalLabel(row: Pick<MemoryJournalRow, "actor" | "kind" | "path" | "added" | "removed">): string {
  const who = ACTORS[row.actor] ?? row.actor;
  const counts = row.kind === "edited" ? ` (+${row.added} −${row.removed})` : "";
  return `${who} ${KINDS[row.kind] ?? row.kind} ${row.path}${counts}`;
}
