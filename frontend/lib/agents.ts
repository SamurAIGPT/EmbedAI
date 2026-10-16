import type { Agent, ApprovalMode } from "./api";

// Suggestions for the model box; any text the Claude CLI accepts also works.
export const MODEL_SUGGESTIONS = ["default", "sonnet", "opus", "haiku"] as const;

export const APPROVAL_MODES: { value: ApprovalMode; label: string; description: string }[] = [
  {
    value: "ask",
    label: "Ask first",
    description: "Claude asks before it writes a file or runs a command. Reading is allowed.",
  },
  {
    value: "accept_edits",
    label: "Accept edits",
    description: "File edits go ahead without asking. Commands still ask first.",
  },
  {
    value: "plan",
    label: "Plan only",
    description: "Claude looks around and writes a plan, but changes nothing.",
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
