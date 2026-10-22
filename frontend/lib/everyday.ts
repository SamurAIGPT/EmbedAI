// Pure helpers for search, attachments, workspace files and usage
// (docs/EVERYDAY.md). No React, no network.

export type Segment = { text: string; mark: boolean };

// A snippet split into plain and marked parts from [start, length] pairs.
// Overlapping or out-of-range pairs are clipped; the text is never parsed as HTML.
// The server counts characters (code points), so an emoji is one, not two UTF-16 units.
export function snippetSegments(snippet: string, highlights: number[][]): Segment[] {
  const chars = Array.from(snippet);
  const spans = highlights
    .filter((h) => Array.isArray(h) && h.length === 2 && h[1] > 0)
    .map(([start, length]) => [Math.max(0, start), Math.min(chars.length, start + length)] as const)
    .filter(([start, end]) => start < end)
    .sort((a, b) => a[0] - b[0]);
  const out: Segment[] = [];
  let at = 0;
  for (const [start, end] of spans) {
    if (end <= at) continue;
    const from = Math.max(start, at);
    if (from > at) out.push({ text: chars.slice(at, from).join(""), mark: false });
    out.push({ text: chars.slice(from, end).join(""), mark: true });
    at = end;
  }
  if (at < chars.length) out.push({ text: chars.slice(at).join(""), mark: false });
  return out;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatTokens(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${(value / 1000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}M`;
}

// "not reported" when the engine said nothing; never 0 for an unknown cost.
export function formatCost(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "not reported";
  if (value === 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

// One line for a finished turn, from the usage its engine reported.
export function turnUsageText(usage: Record<string, unknown> | null | undefined): string {
  if (!usage || typeof usage !== "object") return "Usage not reported";
  const input = count(usage.input_tokens);
  const output = count(usage.output_tokens);
  const cached = (count(usage.cache_read_input_tokens) ?? 0) + (count(usage.cache_creation_input_tokens) ?? 0);
  const cost = typeof usage.cost_usd === "number" && Number.isFinite(usage.cost_usd) ? usage.cost_usd : null;
  const parts: string[] = [];
  if (input !== null) parts.push(`${formatTokens(input)} in`);
  if (output !== null) parts.push(`${formatTokens(output)} out`);
  if (cached > 0) parts.push(`${formatTokens(cached)} cached`);
  if (parts.length === 0 && cost === null) return "Usage not reported";
  parts.push(cost === null ? "cost not reported" : formatCost(cost));
  return parts.join(" · ");
}

// Totals over a conversation's turns, counting only what was reported.
export function conversationUsage(turns: { usage: Record<string, unknown> | null }[]) {
  let tokens = 0;
  let cost: number | null = null;
  let reported = 0;
  for (const turn of turns) {
    const usage = turn.usage;
    if (!usage) continue;
    reported += 1;
    tokens += (count(usage.input_tokens) ?? 0) + (count(usage.output_tokens) ?? 0);
    if (typeof usage.cost_usd === "number" && Number.isFinite(usage.cost_usd)) cost = (cost ?? 0) + usage.cost_usd;
  }
  return { turns: turns.length, reported, tokens, cost };
}

export const IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const TEXT_MAX_BYTES = 1024 * 1024;
export const MAX_ATTACHMENTS = 8;
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const TEXT_EXTENSIONS = [
  ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".xml", ".html", ".css",
  ".js", ".ts", ".tsx", ".py", ".sh", ".sql", ".toml", ".ini",
];

// The same rules the server applies, checked first so a refusal is instant.
// The server checks again (and also looks at the bytes).
export function attachmentProblem(file: { name: string; type: string; size: number }): string | null {
  const name = file.name || "file";
  const lower = name.toLowerCase();
  const isImage = IMAGE_TYPES.includes(file.type) || /\.(png|jpe?g|gif|webp)$/.test(lower);
  if (file.size === 0) return `${name} is empty.`;
  if (isImage) return file.size > IMAGE_MAX_BYTES ? `${name} is over the 10 MB image limit.` : null;
  if (TEXT_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
    return file.size > TEXT_MAX_BYTES ? `${name} is over the 1 MB text file limit.` : null;
  }
  return `${name} can't be attached. Attach images (PNG, JPEG, GIF, WebP) or text files.`;
}

// The parent of a workspace path ("a/b/c" -> "a/b", "a" -> "").
export function parentPath(path: string): string {
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}

export function currentMonth(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function shiftMonth(month: string, delta: number): string {
  const [year, mon] = month.split("-").map(Number);
  const date = new Date(Date.UTC(year, mon - 1 + delta, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}

// What a person can do about a failed turn, by its error code. The engine's own
// error text is shown above this; this line only adds the next step.
const NEXT_STEP: Record<string, string> = {
  cli_unavailable: "Install the engine's command line tool, or set its path in Settings, then send again.",
  engine_not_configured: "Open Settings and finish this engine's setup, then send again.",
  engine_unavailable: "Check the engine's status in Settings, then send again.",
  engine_stalled: "The engine stopped responding. Send the message again; if it keeps happening, check the engine in a terminal.",
  session_resume_failed: "Start a new session to continue this conversation.",
  no_session: "Start a new session to continue this conversation.",
  no_model: "Choose a model for this conversation, then send again.",
  computer_unavailable: "Start the agent's computer from its Computer panel, then send again.",
  engine_without_computers: "This engine can't use the agent's computer. Pick an engine that can, or remove the computer.",
  length: "The answer hit the engine's length limit. Ask for a shorter answer or continue in a new message.",
  runner_error: "Something went wrong starting the engine. Send again; the backend log has details.",
  engine_limited: "Wait for the limit to reset, switch this conversation's agent to another engine, or give the agent a fallback engine in the Agents panel.",
};

export function failureNextStep(errorCode: string): string | null {
  return NEXT_STEP[errorCode] ?? null;
}

// A short chip for an engine that is limited right now: "Usage limit · resets in 2h 5m".
export function engineLimitText(
  limit: { kind: string; resets_at: string | null } | null | undefined,
  now: number = Date.now(),
): string | null {
  if (!limit) return null;
  const what = limit.kind === "credits" ? "Out of credits" : limit.kind === "rate_limit" ? "Rate limited" : "Usage limit";
  const at = limit.resets_at ? Date.parse(limit.resets_at) : NaN;
  if (!Number.isFinite(at) || at <= now) return what;
  const minutes = Math.max(1, Math.round((at - now) / 60_000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  const span = days ? `${days}d${hours ? ` ${hours}h` : ""}` : hours ? `${hours}h${mins ? ` ${mins}m` : ""}` : `${mins}m`;
  return `${what} · resets in ${span}`;
}

// What an interrupted turn says, from why it stopped and how far it had got
// (`replay` on turn.interrupted: before_accept, unknown, after_accept).
export function interruptedText(reason: string, replay: string): string {
  const when = reason === "shutdown" ? "The app was closed" : "The app stopped";
  if (replay === "before_accept") {
    return `${when} before the engine received this message, so nothing ran. It was not sent again: send it again when you are ready.`;
  }
  if (replay === "after_accept") {
    return `${when} while the agent was working. It may already have run tools or changed files, so this message was not resumed or sent again. Check what it did before sending it again.`;
  }
  if (replay === "unknown") {
    return `${when} just after the engine received this message. It may have started working, so this message was not resumed or sent again. Check before sending it again.`;
  }
  return `${when} while the agent was working. This message was not resumed or sent again.`;
}
