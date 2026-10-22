const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

class ApiError extends Error {
  status: number;
  // The backend's machine-readable cause when it gives one (for example
  // "turn_running"); null otherwise.
  code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// The desktop app's backend answers every request that lacks its launch token with
// this code: the page was opened somewhere other than the app's own window.
export function isLaunchTokenMissing(err: unknown): boolean {
  return err instanceof ApiError && err.code === "launch_token_required";
}

function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    // {"error_code": ..., "message": ...}, the shape of errors a client acts on.
    const message = (detail as { message?: unknown }).message;
    return typeof message === "string" && message ? message : fallback;
  }
  if (!Array.isArray(detail)) return fallback;
  const messages = detail.flatMap((issue: unknown) => {
    if (!issue || typeof issue !== "object" || !("msg" in issue) || typeof issue.msg !== "string") {
      return [];
    }
    const location = "loc" in issue && Array.isArray(issue.loc) ? issue.loc.join(".") : "";
    return [location ? `${location}: ${issue.msg}` : issue.msg];
  });
  return messages.join("; ") || fallback;
}

// --- Server-sent events (live local runs) ---
// Reads them from a fetch() body; EventSource is not used because it cannot
// report an HTTP error status.

export type RunEvent = {
  run_id: number;
  seq: number;
  type: string;
  data: Record<string, unknown>;
  ts: string;
};

// What every stream frame has in common: the parser checks nothing else.
export type StreamEnvelope = { type: string; data: Record<string, unknown> };

function parseBlock<T extends StreamEnvelope>(block: string): T | null {
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // blank or keep-alive comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") data.push(value);
  }
  if (data.length === 0) return null;
  try {
    const parsed: unknown = JSON.parse(data.join("\n"));
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as StreamEnvelope).type === "string" &&
      (parsed as StreamEnvelope).data &&
      typeof (parsed as StreamEnvelope).data === "object"
    ) {
      return parsed as T;
    }
  } catch {
    // a frame that is not valid JSON is skipped, not fatal
  }
  return null;
}

// Feed it decoded text in any chunking (a frame, half a frame, "\r\n" split
// across chunks); it calls onEvent once per complete frame.
export function createSseParser<T extends StreamEnvelope = RunEvent>(onEvent: (event: T) => void) {
  let buffer = "";
  return {
    push(chunk: string) {
      buffer = (buffer + chunk).replace(/\r\n/g, "\n");
      // A trailing "\r" may be the first half of "\r\n": keep it for later.
      const heldBack = buffer.endsWith("\r") ? "\r" : "";
      if (heldBack) buffer = buffer.slice(0, -1);
      buffer = buffer.replace(/\r/g, "\n");
      const blocks = buffer.split("\n\n");
      buffer = (blocks.pop() ?? "") + heldBack;
      for (const block of blocks) {
        const event = parseBlock<T>(block);
        if (event) onEvent(event);
      }
    },
  };
}

// --- Owner session (docs/AUTH.md) ---
// The session itself is an HttpOnly cookie the page never sees. Writes carry
// the CSRF token the server returned with the session; it lives in memory only.
let csrfToken: string | null = null;
let onSignedOut: (() => void) | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

// Called when the server says this browser is not (or no longer) signed in.
export function setSignedOutHandler(handler: (() => void) | null) {
  onSignedOut = handler;
}

export function isSignedOutError(err: unknown): boolean {
  return err instanceof ApiError && (err.code === "auth_required" || err.code === "session_invalid");
}

function authHeaders(method: string | undefined): Record<string, string> {
  const safe = !method || ["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
  return !safe && csrfToken ? { "X-CSRF-Token": csrfToken } : {};
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...authHeaders(init?.method), ...(init?.headers ?? {}) },
  });

  if (!res.ok) {
    let detail = res.statusText;
    let code: string | null = null;
    try {
      const body = await res.json();
      detail = formatErrorDetail(body?.detail, detail);
      if (typeof body?.detail?.error_code === "string") code = body.detail.error_code;
    } catch {
      // response body wasn't JSON — fall back to statusText
    }
    const error = new ApiError(res.status, detail, code);
    if (res.status === 401 && isSignedOutError(error)) onSignedOut?.();
    throw error;
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// For a body that is not JSON (a CSV export). Errors are read like request()'s.
async function requestText(path: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!res.ok) {
    let detail = res.statusText;
    let code: string | null = null;
    try {
      const body = await res.json();
      detail = formatErrorDetail(body?.detail, detail);
      if (typeof body?.detail?.error_code === "string") code = body.detail.error_code;
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, detail, code);
  }
  return res.text();
}

export type DecisionEvent =
  | "shown"
  | "allowed"
  | "denied"
  | "auto_allowed"
  | "expired"
  | "cancelled"
  | "undone";

export type DecisionFilter = {
  limit?: number;
  // The page cursor: `next_before` of the previous page.
  before?: string | null;
  event?: DecisionEvent | null;
  thread_id?: number | null;
};

export function decisionQuery(filter: DecisionFilter): string {
  const params = new URLSearchParams();
  if (filter.limit != null) params.set("limit", String(filter.limit));
  if (filter.before) params.set("before", filter.before);
  if (filter.event) params.set("event", filter.event);
  if (filter.thread_id != null) params.set("thread_id", String(filter.thread_id));
  const query = params.toString();
  return query ? `?${query}` : "";
}

// One line of the decision log. Never the call's full input: `summary` is one
// line with secrets removed.
export type DecisionEntry = {
  id: string;
  at: string;
  event: DecisionEvent;
  thread_id: number | null;
  turn_id: number | null;
  approval_id: string | null;
  tool: string | null;
  kind: string | null;
  summary: string | null;
  // user | saved_rule | timeout | provider
  source: string | null;
  rule: string | null;
  remembered: boolean | null;
  mode?: string | null;
};

export type DecisionsPage = { items: DecisionEntry[]; next_before: string | null };

export type ApprovalRule = {
  id: number;
  match: "exact" | "prefix";
  command: string;
  // null: applies in every folder.
  workspace: string | null;
  created_at: string;
};

export type ApprovalRuleInput = {
  match: "exact" | "prefix";
  command: string;
  workspace?: string | null;
};

export type ApprovalAnswer = {
  status: string;
  decision: "allow" | "deny";
  rule_id?: number | null;
  remember_error?: string | null;
};

export type SentinelIssue = {
  sig: string;
  kind: "tool_error" | "retry_loop" | "repeated_denial" | "turn_failure";
  // Normalized: paths, numbers, ids and quoted text replaced. Never raw output.
  title: string;
  severity: "high" | "medium" | "low";
  tool: string | null;
  thread_id: number | null;
  turn_id: number | null;
  count: number;
  first_at: string;
  last_at: string;
  evidence: { source: string; id: string | number };
};

export type SentinelReport = {
  since: string;
  scanned_events: number;
  scanned_decisions: number;
  issues: SentinelIssue[];
};

export type LinkCode = {
  code: string;
  expires_at: string;
  bot_username: string | null;
};

export type LinkStatus = {
  status: "pending" | "claimed" | "expired";
  user_id: string | null;
};

export type Sandbox = {
  box_id: string;
  state: string | null;
  machine_type: string | null;
  user_id: string | null;
  ttl_seconds: number | null;
  expires_at: string | null;
  is_active: boolean | null;
};

export type AuthStatus = { connected: boolean };

export type Schedule = {
  id: number;
  user_id: string;
  provider: string;
  box_id: string;
  prompt_text: string;
  cron_expression: string;
  timezone: string;
  is_active: boolean;
  next_run_at: string;
  last_run_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  paused_reason: string | null;
  created_at: string;
  updated_at: string;
};

export type DeviceLogin = { url: string; code: string };

export type Task = {
  id: number;
  user_id: string;
  provider: string;
  box_id: string;
  prompt_text: string;
  status:
    | "queued"
    | "starting"
    | "pending"
    | "running"
    | "waiting_approval"
    | "succeeded"
    | "failed";
  prompt_id: string | null;
  session_id: string | null;
  schedule_id: number | null;
  scheduled_for: string | null;
  result: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type LocalStatus = {
  // LOCAL_EXECUTION_ENABLED on the backend.
  enabled: boolean;
  // Enabled, allowed for this browser, and the Claude CLI is installed and signed in.
  available: boolean;
  reason: string | null;
  cli_version: string | null;
  // LOCAL_TOOLS_ENABLED: Claude may use tools, each call waiting for Allow/Deny.
  // Absent on a backend older than this feature, which means chat only.
  tools_enabled?: boolean;
};

// A tool call local Claude is waiting for a person to allow or deny.
export type LocalApproval = {
  id: string;
  task_id: number;
  tool: string;
  // One line naming what the call acts on (a command, a file path).
  summary: string;
  // The exact input the tool will run with if allowed.
  input: Record<string, unknown>;
  created_at: string;
};

export type Providers = {
  // Boat sandboxes: needs BOAT_API_KEY on the backend. `reason` says why it is off.
  boat: { enabled: boolean; reason: string | null };
};

export type PermissionAsk = {
  id: number;
  task_id: number;
  user_id: string;
  bucket: string;
  connector: string | null;
  tool: string | null;
  status: "pending" | "answered" | "expired";
  decision: "allow" | "deny" | null;
  created_at: string;
  answered_at: string | null;
};

export type ApprovalMode = "ask" | "accept_edits" | "plan";

// An agent's own isolated machine (a Docker container) where its turns run
// their commands and file edits. Older backends never send these fields.
export type ComputerNetwork = "none" | "default";

export type ComputerSummary = {
  id: number;
  provider: string;
  name: string;
  image: string;
  network: ComputerNetwork;
  // missing | creating | running | stopped | error | deleting
  state: string;
};

export type ComputerHealth = {
  provider: string;
  available: boolean;
  version: string | null;
  error: string | null;
};

export type ComputerLastCommand = {
  tool: string | null;
  subject: string | null;
  exit_code: number | null;
  timed_out: boolean;
  output: string;
  at: string | null;
};

export type Computer = ComputerSummary & {
  agent_id: number;
  image_pinned: boolean;
  image_id: string | null;
  cpus: number;
  memory_mb: number;
  pids_limit: number;
  idle_minutes: number;
  auto_start: boolean;
  // What Docker reports now; null when it was not asked or cannot be reached.
  live_state: string | null;
  needs_recreate: boolean;
  problem: string | null;
  last_error: string | null;
  workspace_path: string | null;
  guest_workspace: string;
  container_id: string | null;
  busy: boolean;
  last_used_at: string | null;
  last_command: ComputerLastCommand | null;
  health: ComputerHealth | null;
};

export type ComputerSettings = {
  image?: string;
  cpus?: number;
  memory_mb?: number;
  pids_limit?: number;
  network?: ComputerNetwork;
  idle_minutes?: number;
  auto_start?: boolean;
};

// A named agent: instructions, model, workspace and how much it asks first.
// Conversations started with it keep using it.
export type Agent = {
  id: number;
  name: string;
  // A short role line and a longer description; neither is sent to Claude.
  title?: string | null;
  description?: string | null;
  instructions: string | null;
  provider: string;
  model: string | null;
  workspace_path: string | null;
  approval_mode: ApprovalMode;
  pinned?: boolean;
  // Off: the agent's notes (MEMORY.md) are not loaded into its conversations.
  memory_enabled?: boolean;
  // Off: no notes or earlier conversations are recalled before a turn.
  recall_enabled?: boolean;
  // Another engine (and model) a turn moves to when this agent's engine
  // refuses it for a limit before starting on it (docs/ENGINES.md).
  fallback_provider?: string | null;
  fallback_model?: string | null;
  archived_at: string | null;
  created_at?: string;
  updated_at?: string;
  // Its computer, when it has one; its turns then run their tools there.
  computer_id?: number | null;
  computer?: ComputerSummary | null;
};

export type AgentInput = {
  name: string;
  title?: string | null;
  description?: string | null;
  instructions?: string;
  provider?: string;
  model?: string | null;
  workspace_path?: string | null;
  approval_mode?: ApprovalMode;
  pinned?: boolean;
  memory_enabled?: boolean;
  recall_enabled?: boolean;
  fallback_provider?: string | null;
  fallback_model?: string | null;
  // Create only: the first contents of the agent's notes.
  starter_notes?: string | null;
};

export type EngineModel = { id: string; label: string; default: boolean };
export type Engine = {
  id: string;
  label: string;
  kind: "cli" | "acp" | "api";
  installed: boolean;
  version: string | null;
  signed_in: boolean | null;
  available: boolean;
  reason: string | null;
  models: EngineModel[];
  default_model: string | null;
  custom_models: boolean;
  capabilities: { tools: boolean; approvals: boolean; mcp: boolean; images: boolean; resume: boolean; computers: boolean };
  configurable: boolean;
  config: Record<string, unknown> | null;
  // Set while the engine refuses turns for a usage or rate limit or no credits.
  limit?: EngineLimit | null;
};
export type EngineLimit = {
  kind: "usage_limit" | "rate_limit" | "credits";
  resets_at: string | null;
  since: string;
  message: string;
};
export type EngineConfigInput = {
  command?: string;
  label?: string;
  base_url?: string;
  model?: string;
  api_key?: string;
  clear_api_key?: boolean;
};

// Topic notes (memory/<topic>.md), the journal and proposed notes (docs/MEMORY.md).
export type TopicInfo = { name: string; title: string | null; aliases: string[]; bytes: number; modified_at: string };
export type TopicList = { topics: TopicInfo[]; max_topics: number; max_bytes: number; folder: string };
export type TopicDoc = {
  name: string;
  path: string;
  text: string;
  // Send back as expected_hash; a note not created yet has the hash of "".
  hash: string;
  bytes: number;
  exists: boolean;
  redacted: number;
};
export type MemoryJournalRow = {
  id: string;
  at: string;
  path: string;
  actor: string;
  kind: "created" | "edited" | "deleted";
  before_hash: string | null;
  after_hash: string | null;
  diff: string;
  added: number;
  removed: number;
  can_undo: boolean;
  reason: string | null;
};
export type MemoryProposal = {
  id: string;
  at: string;
  text: string;
  line: string;
  target: string;
  thread_id: number | null;
  thread_title: string | null;
  turn_id: number | null;
};

// An agent's notes and how much of them loads into each turn.
export type AgentMemory = {
  path: string;
  text: string;
  // Send back as expected_hash so a save never overwrites the agent's own edit.
  hash: string;
  lines: number;
  bytes: number;
  loaded_lines: number;
  loaded_bytes: number;
  truncated: boolean;
  max_lines: number;
  max_bytes: number;
  enabled: boolean;
  // After a save: how many things that looked like secrets were replaced.
  redacted: number;
};

// One agent as a portable file. `redacted` names the parts where something that
// looked like a secret was replaced.
export type AgentExport = {
  file_name: string;
  document: Record<string, unknown>;
  redacted: string[];
};

export type Thread = {
  id: number;
  // Present once the backend supports named agents; null for a plain chat.
  agent_id?: number | null;
  title: string | null;
  provider: string;
  model: string | null;
  // The folder Claude works in; saved command rules can be tied to it.
  workspace_path?: string | null;
  // The computer this conversation's tools run in (its agent's), or null.
  computer?: ComputerSummary | null;
  // new | active | broken | none. "broken": the Claude session cannot be found
  // and must be reset before the thread takes another message.
  session_state: string;
  last_seq: number;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ThreadTurn = {
  id: number;
  seq: number;
  prompt_text: string;
  // running | succeeded | failed | cancelled | interrupted
  status: string;
  error_code: string | null;
  error: string | null;
  result: string | null;
  model: string | null;
  usage: Record<string, unknown> | null;
  started_at: string;
  finished_at: string | null;
};

export type ThreadDetail = Thread & {
  turns: ThreadTurn[];
  active_turn_id: number | null;
};

// One frame of a thread's stream. Stored events have a seq and a timestamp;
// typing frames (text.delta) and runtime.status have neither.
export type ThreadEvent = {
  thread_id: number;
  seq?: number;
  type: string;
  turn_id?: number | null;
  data: Record<string, unknown>;
  ts?: string;
};

// A GET that returns server-sent events. No Content-Type header: it is a plain
// GET and stays a simple cross-origin request (Last-Event-ID, when given, is
// the only extra header). Resolves when the stream ends for any reason; a
// dropped connection rejects, it is never a success.
async function readEventStream<T extends StreamEnvelope>(
  path: string,
  extraHeaders: Record<string, string>,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
  onOpen?: () => void
): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "text/event-stream", ...extraHeaders },
    credentials: "include",
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, res.statusText || "Could not open the live stream");
  }
  onOpen?.();
  const parser = createSseParser<T>(onEvent);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.push(decoder.decode(value, { stream: true }));
  }
  parser.push(decoder.decode());
}

export type ApplicationSetting = {
  key: string;
  value: unknown;
  effective_value: unknown;
  source: "database" | "default" | "environment";
  default: unknown;
  applies: "immediate" | "next_run" | "restart";
  sensitive: boolean;
  version: number;
  error: string | null;
  resolved_path?: string | null;
};

export type DesktopSettings = {
  settings: ApplicationSetting[];
  storage: Record<string, string>;
};

// --- Routines (docs/ROUTINES.md) ---

export type ScheduleKind = "cron" | "weekdays" | "interval" | "once" | "manual";
export type RoutineRunState =
  | "queued"
  | "running"
  | "needs_input"
  | "completed"
  | "failed"
  | "missed"
  | "skipped"
  | "interrupted";

export type RoutineRun = {
  id: number;
  routine_id: number;
  trigger: "schedule" | "manual" | "webhook" | "retry";
  state: RoutineRunState;
  scheduled_for: string | null;
  thread_id: number | null;
  turn_id: number | null;
  result: string | null;
  error: string | null;
  waiting_since: string | null;
  waited_seconds: number;
  attempt: number;
  retry_of: number | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type Routine = {
  id: number;
  agent_id: number;
  name: string;
  prompt: string;
  schedule_kind: ScheduleKind;
  schedule_spec: Record<string, unknown>;
  timezone: string;
  paused: boolean;
  paused_reason: string | null;
  next_run_at: string | null;
  run_limit_minutes: number;
  approval_mode: ApprovalMode;
  approval_timeout_minutes: number;
  catch_up_minutes: number;
  retry_limit: number;
  retry_delay_minutes: number;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
  summary: string;
  next_dates: string[];
  webhook_enabled: boolean;
  latest_run: RoutineRun | null;
};

export type RoutineInput = {
  agent_id: number;
  name: string;
  prompt: string;
  schedule_kind: ScheduleKind;
  schedule_spec: Record<string, unknown>;
  timezone: string;
  run_limit_minutes: number;
  approval_mode: ApprovalMode;
  approval_timeout_minutes: number;
  catch_up_minutes: number;
  retry_limit: number;
  retry_delay_minutes: number;
};

export type SchedulePreview = { summary: string; next_dates: string[] };
export type WebhookSecret = { routine: Routine; secret: string; path: string };

export type AppNotification = {
  id: number;
  kind: string;
  title: string;
  body: string | null;
  routine_id: number | null;
  run_id: number | null;
  thread_id: number | null;
  read_at: string | null;
  created_at: string;
};

export const api = {
  getEngines: (refresh = false) => request<Engine[]>(`/engines${refresh ? "?refresh=true" : ""}`),
  saveEngineConfig: (engineId: string, config: EngineConfigInput) =>
    request<Engine>(`/engines/${encodeURIComponent(engineId)}/config`, { method: "PUT", body: JSON.stringify(config) }),
  deleteEngineConfig: (engineId: string) => request<void>(`/engines/${encodeURIComponent(engineId)}/config`, { method: "DELETE" }),
  getDesktopSettings: () => request<DesktopSettings>("/settings"),
  saveDesktopSetting: (key: string, value: unknown, expectedVersion: number) =>
    request<ApplicationSetting>(`/settings/${encodeURIComponent(key)}`, {
      method: "PUT", body: JSON.stringify({ value, expected_version: expectedVersion }),
    }),
  testDesktopClaude: () => request<{ available: boolean; version: string | null; error: string | null }>("/settings/test/claude", { method: "POST" }),
  createLinkCode: () => request<LinkCode>("/link", { method: "POST" }),

  getLinkStatus: (code: string) => request<LinkStatus>(`/link/${code}`),

  ensureSandbox: (userId: string) =>
    request<Sandbox>("/sandboxes/ensure", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),

  getClaudeStatus: (boxId: string) =>
    request<AuthStatus>(`/sandboxes/${boxId}/auth/claude/status`),

  getAuthStatus: (boxId: string, provider: string) =>
    request<AuthStatus>(`/sandboxes/${boxId}/auth/${provider}/status`),

  startDeviceLogin: (boxId: string, provider: string) =>
    request<DeviceLogin>(`/sandboxes/${boxId}/auth/${provider}/device/start`, {
      method: "POST",
    }),

  startClaudeLogin: (boxId: string) =>
    request<{ login_url: string }>(`/sandboxes/${boxId}/auth/claude/start`, {
      method: "POST",
    }),

  submitClaudeCode: (boxId: string, code: string) =>
    request<{ status: string }>(`/sandboxes/${boxId}/auth/claude/submit`, {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  submitTask: (
    userId: string,
    boxId: string,
    promptText: string,
    parentTaskId?: number
  ) =>
    request<Task>("/tasks", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        provider: "claude",
        box_id: boxId,
        prompt_text: promptText,
        parent_task_id: parentTaskId ?? null,
      }),
    }),

  getTask: (taskId: number) => request<Task>(`/tasks/${taskId}`),

  getProviders: () => request<Providers>("/providers"),

  getLocalStatus: () => request<LocalStatus>("/local/status"),

  createLocalTask: (userId: string, promptText: string) =>
    request<Task>("/local/tasks", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, prompt_text: promptText }),
    }),

  getLocalTask: (taskId: number) => request<Task>(`/local/tasks/${taskId}`),

  listLocalTasks: (userId: string) =>
    request<Task[]>(`/local/tasks?user_id=${encodeURIComponent(userId)}`),

  listLocalApprovals: (taskId: number) =>
    request<LocalApproval[]>(`/local/tasks/${taskId}/approvals`),

  // 404 means the question is no longer waiting (answered, expired or the run ended).
  answerLocalApproval: (taskId: number, approvalId: string, decision: "allow" | "deny") =>
    request<{ status: string; decision: string }>(
      `/local/tasks/${taskId}/approvals/${encodeURIComponent(approvalId)}`,
      { method: "POST", body: JSON.stringify({ decision }) }
    ),

  // Live events for one local run. Resolves when the stream ends for any
  // reason, so the caller must then read the task to learn the real outcome.
  // A dropped connection rejects; it is never a success.
  streamLocalTask: (
    taskId: number,
    onEvent: (event: RunEvent) => void,
    signal?: AbortSignal
  ): Promise<void> =>
    readEventStream<RunEvent>(`/local/tasks/${taskId}/events`, {}, onEvent, signal),

  // --- Durable threads (/threads) ---

  // Newest activity first. Archived conversations are left out unless asked for.
  listThreads: (includeArchived = false) =>
    request<Thread[]>(`/threads${includeArchived ? "?include_archived=true" : ""}`),

  getThread: (threadId: number) => request<ThreadDetail>(`/threads/${threadId}`),

  createThread: (agentId?: number | null, selection?: { provider?: string; model?: string | null }) =>
    request<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({ ...(agentId != null ? { agent_id: agentId } : {}), ...selection }),
    }),

  // --- Named agents (/agents). An older backend answers 404: callers treat that
  // as "no agents here" and keep working. ---

  listAgents: (includeArchived = false) =>
    request<Agent[]>(`/agents${includeArchived ? "?include_archived=true" : ""}`),

  createAgent: (input: AgentInput) =>
    request<Agent>("/agents", { method: "POST", body: JSON.stringify(input) }),

  // Only the fields given change; `archived` archives or restores.
  updateAgent: (agentId: number, change: Partial<AgentInput> & { archived?: boolean }) =>
    request<Agent>(`/agents/${agentId}`, { method: "PATCH", body: JSON.stringify(change) }),

  deleteAgent: (agentId: number) => request<void>(`/agents/${agentId}`, { method: "DELETE" }),

  // A copy with the same settings, named "<name> copy". Notes are not copied.
  duplicateAgent: (agentId: number) =>
    request<Agent>(`/agents/${agentId}/duplicate`, { method: "POST" }),

  getAgentMemory: (agentId: number) => request<AgentMemory>(`/agents/${agentId}/memory`),

  // 409 memory_changed when the notes changed since `expectedHash` was read;
  // leave it out to overwrite on purpose.
  saveAgentMemory: (agentId: number, text: string, expectedHash?: string | null) =>
    request<AgentMemory>(`/agents/${agentId}/memory`, {
      method: "PUT",
      body: JSON.stringify(expectedHash ? { text, expected_hash: expectedHash } : { text }),
    }),

  listTopics: (agentId: number) => request<TopicList>(`/agents/${agentId}/memory/topics`),

  getTopic: (agentId: number, name: string) =>
    request<TopicDoc>(`/agents/${agentId}/memory/topics/${encodeURIComponent(name)}`),

  // 409 memory_changed when the note changed since `expectedHash` was read.
  saveTopic: (agentId: number, name: string, text: string, expectedHash?: string | null) =>
    request<TopicDoc>(`/agents/${agentId}/memory/topics/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify(expectedHash ? { text, expected_hash: expectedHash } : { text }),
    }),

  deleteTopic: (agentId: number, name: string) =>
    request<void>(`/agents/${agentId}/memory/topics/${encodeURIComponent(name)}`, { method: "DELETE" }),

  memoryJournal: (agentId: number) => request<MemoryJournalRow[]>(`/agents/${agentId}/memory/journal`),

  undoMemoryChange: (agentId: number, entryId: string) =>
    request<MemoryJournalRow | null>(`/agents/${agentId}/memory/journal/${encodeURIComponent(entryId)}/undo`, {
      method: "POST",
    }),

  memoryProposals: (agentId: number) => request<MemoryProposal[]>(`/agents/${agentId}/memory/proposals`),

  acceptProposal: (agentId: number, proposalId: string, line?: string) =>
    request<{ path: string; hash: string }>(
      `/agents/${agentId}/memory/proposals/${encodeURIComponent(proposalId)}/accept`,
      { method: "POST", body: JSON.stringify(line ? { line } : {}) }
    ),

  rejectProposal: (agentId: number, proposalId: string) =>
    request<void>(`/agents/${agentId}/memory/proposals/${encodeURIComponent(proposalId)}/reject`, { method: "POST" }),

  exportAgent: (agentId: number, includeNotes = false) =>
    request<AgentExport>(`/agents/${agentId}/export${includeNotes ? "?include_notes=true" : ""}`),

  // A new agent from an agent file; it always starts on "Ask first".
  // 422 invalid_agent_file when the file is not one.
  importAgent: (document: unknown, includeNotes = true) =>
    request<Agent>("/agents/import", {
      method: "POST",
      body: JSON.stringify({ document, include_notes: includeNotes }),
    }),

  // --- Agent computers. An older backend answers 404 here: callers treat that
  // as "computers are not available" and keep working. ---

  computersHealth: () => request<ComputerHealth>("/computers/health"),

  getAgentComputer: (agentId: number) => request<Computer>(`/agents/${agentId}/computer`),

  // Idempotent: an agent that already has a computer gets it back unchanged.
  createAgentComputer: (agentId: number, settings: ComputerSettings & { start?: boolean } = {}) =>
    request<Computer>(`/agents/${agentId}/computer`, { method: "POST", body: JSON.stringify(settings) }),

  getComputer: (computerId: number) => request<Computer>(`/computers/${computerId}`),

  updateComputer: (computerId: number, change: ComputerSettings) =>
    request<Computer>(`/computers/${computerId}`, { method: "PATCH", body: JSON.stringify(change) }),

  // start (makes it from a local image if missing), stop, recreate, or
  // prepare-image (the only step that downloads).
  computerAction: (computerId: number, action: "start" | "stop" | "recreate" | "prepare-image") =>
    request<Computer>(`/computers/${computerId}/${action}`, { method: "POST" }),

  // removed: false when Docker is not answering; it is removed later.
  deleteComputer: (computerId: number) =>
    request<{ removed: boolean }>(`/computers/${computerId}`, { method: "DELETE" }),

  // 202 once the turn has started; its answer arrives on the stream. Errors with
  // a code: turn_running, session_broken, provider_unavailable (see ApiError.code).
  // `attachmentIds` from uploadAttachment. 422 images_unsupported: this
  // conversation's engine cannot read images.
  sendTurn: (threadId: number, promptText: string, attachmentIds: string[] = []) =>
    request<ThreadTurn>(`/threads/${threadId}/turns`, {
      method: "POST",
      body: JSON.stringify(
        attachmentIds.length ? { prompt_text: promptText, attachment_ids: attachmentIds } : { prompt_text: promptText }
      ),
    }),

  // Stop the running answer. 202 once Claude was told to stop; the end arrives as
  // a turn.cancelled event. 409 no_running_turn: it had already ended.
  // 409 turn_not_stoppable: it is running in another process.
  stopThread: (threadId: number) =>
    request<{ status: string; turn_id: number }>(`/threads/${threadId}/stop`, { method: "POST" }),

  // Rename and/or archive. 409 turn_running: stop the answer first (archive only).
  updateThread: (threadId: number, change: { title?: string; archived?: boolean; model?: string | null }) =>
    request<Thread>(`/threads/${threadId}`, { method: "PATCH", body: JSON.stringify(change) }),

  // Permanent: the conversation's messages, tool calls and approvals in this app's
  // database. 409 turn_running: stop the answer first.
  deleteThread: (threadId: number) =>
    request<void>(`/threads/${threadId}`, { method: "DELETE" }),

  // The way out of session_state "broken": a new Claude session, same transcript.
  resetThreadSession: (threadId: number) =>
    request<Thread>(`/threads/${threadId}/session/reset`, { method: "POST" }),

  // 409 approval_not_waiting: already answered, expired, or its turn ended.
  // `remember` with an allow also saves the exact shell command as an "always
  // allow" rule for the conversation's folder; the answer says which rule, or
  // why none was saved (it was then allowed this once only).
  answerThreadApproval: (
    threadId: number,
    approvalId: string,
    decision: "allow" | "deny",
    remember = false
  ) =>
    request<ApprovalAnswer>(
      `/threads/${threadId}/approvals/${encodeURIComponent(approvalId)}`,
      { method: "POST", body: JSON.stringify(remember ? { decision, remember } : { decision }) }
    ),

  // --- Approval rules, the decision log and the health scan (local only) ---

  // With `workspace`: the rules that apply in that folder (its own and the
  // ones with no folder).
  listApprovalRules: (workspace?: string | null) =>
    request<ApprovalRule[]>(
      `/approval-rules${workspace ? `?workspace=${encodeURIComponent(workspace)}` : ""}`
    ),

  // 422 with a reason: a credential, the safety floor, shell operators in a prefix...
  addApprovalRule: (input: ApprovalRuleInput) =>
    request<ApprovalRule>("/approval-rules", { method: "POST", body: JSON.stringify(input) }),

  // Logged as `undone`; `threadId` says which conversation it was undone from.
  deleteApprovalRule: (ruleId: number, threadId?: number | null) =>
    request<void>(
      `/approval-rules/${ruleId}${threadId != null ? `?thread_id=${threadId}` : ""}`,
      { method: "DELETE" }
    ),

  listDecisions: (filter: DecisionFilter = {}) =>
    request<DecisionsPage>(`/decisions${decisionQuery(filter)}`),

  // The same filters as listDecisions, as CSV text (newest first).
  exportDecisionsCsv: (filter: DecisionFilter = {}) =>
    requestText(`/decisions/export.csv${decisionQuery({ ...filter, before: undefined, limit: undefined })}`),

  scanHealth: (hours = 24) => request<SentinelReport>(`/sentinel/scan?hours=${hours}`),

  // Every stored event after `afterSeq` (null: from the start), then live. The
  // server replays in order, so a reconnect with the last seq seen misses nothing
  // and repeats nothing. Resolves when the stream ends for any reason.
  streamThread: (
    threadId: number,
    afterSeq: number | null,
    onEvent: (event: ThreadEvent) => void,
    signal?: AbortSignal,
    onOpen?: () => void
  ): Promise<void> =>
    readEventStream<ThreadEvent>(
      `/threads/${threadId}/events/stream`,
      afterSeq === null ? {} : { "Last-Event-ID": String(afterSeq) },
      onEvent,
      signal,
      onOpen
    ),

  getPendingAsk: (taskId: number) =>
    request<PermissionAsk | null>(`/permissions/ask/task/${taskId}`),

  answerAsk: (askId: number, decision: "allow" | "deny", always: boolean) =>
    request<PermissionAsk>(`/permissions/ask/${askId}/answer`, {
      method: "POST",
      body: JSON.stringify({ decision, always }),
    }),

  listTasks: (userId: string) =>
    request<Task[]>(`/tasks?user_id=${encodeURIComponent(userId)}`),

  createSchedule: (
    userId: string,
    boxId: string,
    promptText: string,
    cronExpression: string,
    timezone: string
  ) =>
    request<Schedule>("/schedules", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        provider: "claude",
        box_id: boxId,
        prompt_text: promptText,
        cron_expression: cronExpression,
        timezone,
      }),
    }),

  listSchedules: (userId: string) =>
    request<Schedule[]>(`/schedules?user_id=${encodeURIComponent(userId)}`),

  pauseSchedule: (userId: string, scheduleId: number) =>
    request<Schedule>(
      `/schedules/${scheduleId}/pause?user_id=${encodeURIComponent(userId)}`,
      { method: "POST" }
    ),

  resumeSchedule: (userId: string, scheduleId: number) =>
    request<Schedule>(
      `/schedules/${scheduleId}/resume?user_id=${encodeURIComponent(userId)}`,
      { method: "POST" }
    ),

  deleteSchedule: (userId: string, scheduleId: number) =>
    request<void>(
      `/schedules/${scheduleId}?user_id=${encodeURIComponent(userId)}`,
      { method: "DELETE" }
    ),
  listRoutines: () => request<Routine[]>("/routines"),
  getRoutine: (id: number) => request<Routine>(`/routines/${id}`),
  createRoutine: (input: RoutineInput & { paused?: boolean }) =>
    request<Routine>("/routines", { method: "POST", body: JSON.stringify(input) }),
  updateRoutine: (id: number, input: Partial<RoutineInput>) =>
    request<Routine>(`/routines/${id}`, { method: "PATCH", body: JSON.stringify(input) }),
  deleteRoutine: (id: number) => request<void>(`/routines/${id}`, { method: "DELETE" }),
  pauseRoutine: (id: number) => request<Routine>(`/routines/${id}/pause`, { method: "POST" }),
  resumeRoutine: (id: number) => request<Routine>(`/routines/${id}/resume`, { method: "POST" }),
  runRoutineNow: (id: number) => request<RoutineRun>(`/routines/${id}/run`, { method: "POST" }),
  listRoutineRuns: (id: number, limit = 50) => request<RoutineRun[]>(`/routines/${id}/runs?limit=${limit}`),
  previewSchedule: (kind: ScheduleKind, spec: Record<string, unknown>, timezone: string) =>
    request<SchedulePreview>("/routines/preview", {
      method: "POST",
      body: JSON.stringify({ schedule_kind: kind, schedule_spec: spec, timezone }),
    }),
  enableRoutineWebhook: (id: number) => request<WebhookSecret>(`/routines/${id}/webhook`, { method: "POST" }),
  disableRoutineWebhook: (id: number) => request<Routine>(`/routines/${id}/webhook`, { method: "DELETE" }),
  listNotifications: (limit = 30) => request<AppNotification[]>(`/notifications?limit=${limit}`),
  unreadNotifications: () => request<{ unread: number }>("/notifications/unread-count"),
  readNotification: (id: number) => request<void>(`/notifications/${id}/read`, { method: "POST" }),
  readAllNotifications: () => request<{ unread: number }>("/notifications/read-all", { method: "POST" }),

  // --- Everyday features (docs/EVERYDAY.md) ---
  searchConversations: (query: string, filter: SearchFilter = {}) =>
    request<SearchResponse>(`/search${searchQuery(query, filter)}`),
  uploadAttachment: (threadId: number, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return requestForm<UploadedAttachment>(`/threads/${threadId}/attachments`, form);
  },
  // Object URLs for bytes behind the session (an <img> cannot send the CSRF or
  // launch headers some setups need). The caller revokes them.
  attachmentBlobUrl: (threadId: number, attachmentId: string) =>
    requestBlobUrl(`/threads/${threadId}/attachments/${encodeURIComponent(attachmentId)}`),
  listWorkspaceFiles: (threadId: number, path = "") =>
    request<FolderListing>(`/threads/${threadId}/files?path=${encodeURIComponent(path)}`),
  previewWorkspaceFile: (threadId: number, path: string) =>
    request<FilePreview>(`/threads/${threadId}/files/content?path=${encodeURIComponent(path)}`),
  workspaceImageUrl: (threadId: number, path: string) =>
    requestBlobUrl(`/threads/${threadId}/files/raw?path=${encodeURIComponent(path)}`),
  workspaceDownloadUrl: (threadId: number, path: string) =>
    requestBlobUrl(`/threads/${threadId}/files/download?path=${encodeURIComponent(path)}`),
  // A folder (the whole workspace for "") as a zip; secrets and links out are left out.
  workspaceZipUrl: (threadId: number, path: string) =>
    requestBlobUrl(`/threads/${threadId}/files/zip?path=${encodeURIComponent(path)}`),
  getUsage: (month?: string) => request<UsageSummary>(`/usage${month ? `?month=${encodeURIComponent(month)}` : ""}`),
  usageCsv: (month: string) => requestText(`/usage.csv?month=${encodeURIComponent(month)}`),
};

export type SearchFilter = { agentId?: number | null; since?: string; until?: string; includeArchived?: boolean; limit?: number };

export function searchQuery(query: string, filter: SearchFilter = {}): string {
  const params = new URLSearchParams({ q: query });
  if (filter.agentId != null) params.set("agent_id", String(filter.agentId));
  if (filter.since) params.set("since", filter.since);
  if (filter.until) params.set("until", filter.until);
  if (filter.includeArchived) params.set("include_archived", "true");
  if (filter.limit != null) params.set("limit", String(filter.limit));
  return `?${params.toString()}`;
}

export type SearchHit = {
  thread_id: number;
  thread_title: string | null;
  agent_id: number | null;
  archived: boolean;
  kind: "title" | "user" | "assistant";
  at: string;
  snippet: string;
  // [start, length] pairs into `snippet`.
  highlights: number[][];
  seq: number | null;
  turn_id: number | null;
};

export type SearchResponse = { query: string; hits: SearchHit[] };

export type UploadedAttachment = { id: string; name: string; mime: string; bytes: number; kind: "image" | "text" };

export type FileEntry = {
  name: string;
  path: string;
  // "hidden": a secret file, listed but never shown.
  type: "dir" | "file" | "hidden";
  bytes: number | null;
  modified: string | null;
};

export type FolderListing = { path: string; entries: FileEntry[]; truncated: boolean };

export type FilePreview = {
  path: string;
  name: string;
  bytes: number;
  mime: string;
  kind: "text" | "image" | "binary";
  text: string | null;
  truncated: boolean;
};

export type UsageTotals = {
  key: string;
  label: string;
  turns: number;
  reported_turns: number;
  unreported_turns: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  // null: no turn in this group reported a cost.
  cost_usd: number | null;
  thread_id?: number;
  provider?: string;
  model?: string;
};

export type UsageSummary = {
  month: string;
  total: UsageTotals;
  by_thread: UsageTotals[];
  by_engine: UsageTotals[];
  by_day: UsageTotals[];
};

async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  let code: string | null = null;
  try {
    const body = await res.json();
    detail = formatErrorDetail(body?.detail, detail);
    if (typeof body?.detail?.error_code === "string") code = body.detail.error_code;
  } catch {
    // not JSON
  }
  const error = new ApiError(res.status, detail, code);
  if (res.status === 401 && isSignedOutError(error)) onSignedOut?.();
  return error;
}

// A multipart POST: the browser sets the Content-Type with its boundary.
async function requestForm<T>(path: string, body: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body,
    credentials: "include",
    headers: authHeaders("POST"),
  });
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as T;
}

async function requestBlobUrl(path: string): Promise<string> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw await errorFrom(res);
  return URL.createObjectURL(await res.blob());
}

export { ApiError };
