const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function formatErrorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
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

function parseBlock(block: string): RunEvent | null {
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
      typeof (parsed as RunEvent).type === "string" &&
      (parsed as RunEvent).data &&
      typeof (parsed as RunEvent).data === "object"
    ) {
      return parsed as RunEvent;
    }
  } catch {
    // a frame that is not valid JSON is skipped, not fatal
  }
  return null;
}

// Feed it decoded text in any chunking (a frame, half a frame, "\r\n" split
// across chunks); it calls onEvent once per complete frame.
export function createSseParser(onEvent: (event: RunEvent) => void) {
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
        const event = parseBlock(block);
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
    try {
      const body = await res.json();
      detail = formatErrorDetail(body?.detail, detail);
    } catch {
      // response body wasn't JSON — fall back to statusText
    }
    throw new ApiError(res.status, detail);
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
  streamLocalTask: async (
    taskId: number,
    onEvent: (event: RunEvent) => void,
    signal?: AbortSignal
  ): Promise<void> => {
    // No Content-Type header: this is a plain GET and should stay a simple
    // cross-origin request.
    const res = await fetch(`${API_BASE}/local/tasks/${taskId}/events`, {
      headers: { Accept: "text/event-stream" },
      signal,
    });
    if (!res.ok || !res.body) {
      throw new ApiError(res.status, res.statusText || "Could not open the live stream");
    }
    const parser = createSseParser(onEvent);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
  },

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
