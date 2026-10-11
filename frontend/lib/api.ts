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
