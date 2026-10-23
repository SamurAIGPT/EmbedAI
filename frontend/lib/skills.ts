// An agent's saved skills: reviewed Markdown instructions a person attaches to a message
// on purpose. Nothing here runs by itself and a skill never triggers on its own.

export const MAX_SKILLS_PER_MESSAGE = 3;
export const SKILL_NAME_MAX = 64;

export type SkillSource = "user" | "imported" | "learned" | (string & {});

export type SkillSummary = {
  name: string;
  description: string;
  enabled: boolean;
  source: SkillSource;
  bytes: number;
  updated_at: string | null;
};

export type SkillDoc = {
  name: string;
  description: string;
  body: string;
  enabled: boolean;
  source: SkillSource;
  redacted: number;
  warnings: string[];
};

export type SkillSave = { description: string; body: string; enabled: boolean; source: SkillSource };

export type SkillPreview = { name: string; description: string; body: string; warnings: string[] };

// A skill name: lowercase letters, digits and single hyphens, at most SKILL_NAME_MAX long.
export function slugSkillName(text: string): string {
  return text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, SKILL_NAME_MAX)
    .replace(/-+$/g, "");
}

export function isSkillName(name: string): boolean {
  return name.length > 0 && name.length <= SKILL_NAME_MAX && /^[a-z0-9]+(-[a-z0-9]+)*$/.test(name);
}

// The starting text of a new skill. The person's request, when there is one, seeds the purpose
// and is theirs to correct: it is never sent anywhere or saved until they press Save.
export function skillSkeleton(seed = ""): { name: string; description: string; body: string } {
  const request = seed.trim().replace(/\s+/g, " ");
  const first = request.split(/(?<=[.!?])\s/)[0] ?? "";
  const description = first.length > 160 ? `${first.slice(0, 157).trimEnd()}...` : first;
  const body = [
    "## Purpose",
    request || "What this skill is for, in a sentence or two.",
    "",
    "## Required inputs",
    "- What the person must provide before it starts (files, values, a folder).",
    "",
    "## Steps",
    "1. The steps to follow, in order.",
    "",
    "## Validation checks",
    "- How to confirm the result is right before reporting it.",
    "",
    "## Output format",
    "How the result should be laid out.",
    "",
    "## Approval boundary",
    "- Read and analyse freely. Ask before changing a file, running a command or touching anything outside the workspace.",
    "",
  ].join("\n");
  return { name: slugSkillName(description.split(/\s+/).slice(0, 5).join(" ")), description, body };
}

// `/name rest of the message`: the skill the person typed and what is left to send.
// Only a name in `known` counts, so an ordinary message that starts with a slash stays as typed.
export function parseSkillCommand(text: string, known: readonly string[]): { name: string; rest: string } | null {
  const match = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+([\s\S]*))?$/.exec(text.trimStart());
  if (!match || !known.includes(match[1])) return null;
  return { name: match[1], rest: (match[2] ?? "").trim() };
}

// Names to offer while the box holds a bare `/prefix` (no space yet), best match first.
export function skillCompletions(text: string, available: readonly SkillSummary[], chosen: readonly string[] = []): SkillSummary[] {
  const match = /^\/([a-z0-9-]*)$/.exec(text);
  if (!match) return [];
  return available.filter((s) => s.enabled && !chosen.includes(s.name) && s.name.startsWith(match[1]));
}

// Adds or removes a name, keeping at most MAX_SKILLS_PER_MESSAGE.
export function toggleSkill(chosen: readonly string[], name: string): string[] {
  if (chosen.includes(name)) return chosen.filter((n) => n !== name);
  return chosen.length >= MAX_SKILLS_PER_MESSAGE ? [...chosen] : [...chosen, name];
}

export function sourceLabel(source: SkillSource): string {
  return source === "imported" ? "Imported" : source === "learned" ? "Learned from a run" : "Written here";
}

// The skills the backend recorded as used on a turn (`skill.used` event data).
export function usedSkills(data: Record<string, unknown>): string[] {
  const names = data.names;
  return Array.isArray(names) ? names.filter((n): n is string => typeof n === "string") : [];
}

export function skillErrorText(err: unknown): string {
  const code = (err as { code?: string | null } | null)?.code;
  if (code === "skill_unavailable") return "A skill you picked is no longer available. Remove it and try again.";
  return err instanceof Error && err.message ? err.message : "Could not reach the backend.";
}
