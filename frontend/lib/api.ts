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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
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
    throw new ApiError(res.status, detail, code);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

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

export type Thread = {
  id: number;
  title: string | null;
  provider: string;
  model: string | null;
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

export const api = {
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

  createThread: () =>
    request<Thread>("/threads", { method: "POST", body: JSON.stringify({}) }),

  // 202 once the turn has started; its answer arrives on the stream. Errors with
  // a code: turn_running, session_broken, provider_unavailable (see ApiError.code).
  sendTurn: (threadId: number, promptText: string) =>
    request<ThreadTurn>(`/threads/${threadId}/turns`, {
      method: "POST",
      body: JSON.stringify({ prompt_text: promptText }),
    }),

  // Stop the running answer. 202 once Claude was told to stop; the end arrives as
  // a turn.cancelled event. 409 no_running_turn: it had already ended.
  // 409 turn_not_stoppable: it is running in another process.
  stopThread: (threadId: number) =>
    request<{ status: string; turn_id: number }>(`/threads/${threadId}/stop`, { method: "POST" }),

  // Rename and/or archive. 409 turn_running: stop the answer first (archive only).
  updateThread: (threadId: number, change: { title?: string; archived?: boolean }) =>
    request<Thread>(`/threads/${threadId}`, { method: "PATCH", body: JSON.stringify(change) }),

  // Permanent: the conversation's messages, tool calls and approvals in this app's
  // database. 409 turn_running: stop the answer first.
  deleteThread: (threadId: number) =>
    request<void>(`/threads/${threadId}`, { method: "DELETE" }),

  // The way out of session_state "broken": a new Claude session, same transcript.
  resetThreadSession: (threadId: number) =>
    request<Thread>(`/threads/${threadId}/session/reset`, { method: "POST" }),

  // 409 approval_not_waiting: already answered, expired, or its turn ended.
  answerThreadApproval: (threadId: number, approvalId: string, decision: "allow" | "deny") =>
    request<{ status: string; decision: string }>(
      `/threads/${threadId}/approvals/${encodeURIComponent(approvalId)}`,
      { method: "POST", body: JSON.stringify({ decision }) }
    ),

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
};

export { ApiError };
