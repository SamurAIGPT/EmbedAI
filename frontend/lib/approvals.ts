// Words for approvals, saved rules and the decision log. Pure: no React, no network.
import type { ApprovalRule, DecisionEntry, DecisionEvent, SentinelIssue } from "./api";

export const DECISION_EVENTS: { value: DecisionEvent; label: string }[] = [
  { value: "shown", label: "Asked" },
  { value: "allowed", label: "Allowed" },
  { value: "denied", label: "Denied" },
  { value: "auto_allowed", label: "Allowed by a saved rule" },
  { value: "expired", label: "Expired" },
  { value: "cancelled", label: "Cancelled" },
  { value: "undone", label: "Always-allow removed" },
];

export function decisionEventLabel(event: string): string {
  return DECISION_EVENTS.find((e) => e.value === event)?.label ?? event;
}

// One line saying what happened and who decided.
export function decisionText(entry: DecisionEntry): string {
  const what = entry.summary || entry.tool || "a tool call";
  switch (entry.event) {
    case "shown":
      return entry.rule?.startsWith("safety_floor:")
        ? `Asked about ${what} (a saved rule matched, but the safety floor holds it for you)`
        : `Asked about ${what}`;
    case "allowed":
      return entry.remembered ? `You allowed ${what} and chose to always allow it` : `You allowed ${what}`;
    case "denied":
      return `You denied ${what}`;
    case "auto_allowed":
      return `${what} ran without asking: a saved rule allowed it`;
    case "expired":
      return `Nobody answered about ${what} in time, so it did not run`;
    case "cancelled":
      return `The question about ${what} was withdrawn`;
    case "undone":
      return `You stopped always allowing ${ruleCommand(entry.rule) || "a command"}`;
    default:
      return what;
  }
}

// "exact@/work: git status" -> "git status".
export function ruleCommand(rule: string | null | undefined): string {
  if (!rule) return "";
  const at = rule.indexOf(": ");
  return at === -1 ? rule : rule.slice(at + 2);
}

export function ruleMatchLabel(rule: Pick<ApprovalRule, "match">): string {
  return rule.match === "prefix" ? "Starts with" : "Exactly";
}

export function ruleScopeLabel(rule: Pick<ApprovalRule, "workspace">): string {
  return rule.workspace ? `In ${rule.workspace}` : "In every folder";
}

// The card offers "Always allow this command" only where the backend can save
// one: a shell command with text.
export function canAlwaysAllow(tool: string, input: Record<string, unknown>): boolean {
  const command = input.command;
  return tool === "Bash" && typeof command === "string" && command.trim() !== "";
}

export function csvFileName(threadId: number | null | undefined, now: Date = new Date()): string {
  const day = now.toISOString().slice(0, 10);
  return threadId != null ? `decisions-thread-${threadId}-${day}.csv` : `decisions-${day}.csv`;
}

const ISSUE_KIND: Record<SentinelIssue["kind"], string> = {
  retry_loop: "Retry loop",
  repeated_denial: "Asked again after a denial",
  tool_error: "Tool error",
  turn_failure: "Failed answer",
};

export function issueKindLabel(kind: string): string {
  return ISSUE_KIND[kind as SentinelIssue["kind"]] ?? kind;
}
