
export type TemplateSkill = { name: string; description: string; body: string };

export type AgentTemplate = {
  id: string;
  name: string;
  role: string;
  category: string;
  description: string;
  instructions: string;
  color: string;
  example_requests: string[];
  requires: string[];
  available: boolean;
  starter_notes: string | null;
  skills: TemplateSkill[];
};

export type TemplateCatalog = { version: number; categories: string[]; templates: AgentTemplate[] };

export const CATEGORY_LABELS: Record<string, string> = {
  everyday: "Everyday",
  work: "Work",
  writing: "Writing",
  design: "Design",
  sales: "Sales",
  personal: "Personal",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? category.charAt(0).toUpperCase() + category.slice(1);
}

// Templates whose name, role, description or category contain every word typed
// (ignoring case), within one category or all of them. Mirrors the backend search,
// so the gallery filters as the person types without another request.
export function filterTemplates(templates: AgentTemplate[], query: string, category: string | null): AgentTemplate[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return templates.filter((template) => {
    if (category && template.category !== category) return false;
    const text = `${template.name} ${template.role} ${template.description} ${template.category}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
}

// "Helper", then "Helper 2", "Helper 3" ... the first name no live agent has, within the 80 characters a name may have.
export function uniqueAgentName(name: string, taken: Iterable<string>, max = 80): string {
  const used = new Set(Array.from(taken, (n) => n.trim().toLowerCase()));
  const base = name.trim().slice(0, max);
  if (!used.has(base.toLowerCase())) return base;
  for (let n = 2; ; n += 1) {
    const suffix = ` ${n}`;
    const candidate = base.slice(0, max - suffix.length).trimEnd() + suffix;
    if (!used.has(candidate.toLowerCase())) return candidate;
  }
}

export function requirementLabel(requirement: string): string {
  return requirement.charAt(0).toUpperCase() + requirement.slice(1);
}

export type SkillInstallResult = { installed: string[]; failed: string[]; unsupported: boolean };

// Installs each chosen skill (`install` is api.installTemplateSkill) on the new agent, one at a time. Never throws: a
// missing skills library (404/405) or any other refusal is reported, and the agent stays.
export async function installTemplateSkills(
  agentId: number,
  templateId: string,
  skills: TemplateSkill[],
  install: (agentId: number, templateId: string, skill: TemplateSkill) => Promise<unknown>,
): Promise<SkillInstallResult> {
  const result: SkillInstallResult = { installed: [], failed: [], unsupported: false };
  for (const skill of skills) {
    try {
      await install(agentId, templateId, skill);
      result.installed.push(skill.name);
    } catch (err) {
      const status = (err as { status?: number }).status;
      if (status === 404 || status === 405) {
        result.unsupported = true;
        result.failed.push(...skills.slice(skills.indexOf(skill)).map((s) => s.name));
        break;
      }
      result.failed.push(skill.name);
    }
  }
  return result;
}

export function skillInstallNotice(result: SkillInstallResult): string | null {
  if (result.failed.length === 0) return null;
  if (result.unsupported) return "The agent was created, but this version has no skills library, so the suggested skills were not added.";
  return `The agent was created, but these skills were not added: ${result.failed.join(", ")}.`;
}
