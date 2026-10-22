// Words and schedule drafts for routines. Pure: no React, no network.
import type { Routine, RoutineRun, RoutineRunState, ScheduleKind } from "./api";

export const SCHEDULE_KINDS: { value: ScheduleKind; label: string }[] = [
  { value: "weekdays", label: "On chosen days at a time" },
  { value: "interval", label: "Every few minutes or hours" },
  { value: "cron", label: "Cron expression (advanced)" },
  { value: "once", label: "Once, at a date and time" },
  { value: "manual", label: "Only by hand or webhook" },
];

export const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

// Every field a schedule editor shows; only the current kind's fields are sent.
export type ScheduleDraft = {
  kind: ScheduleKind;
  days: number[];
  time: string;
  everyMinutes: string;
  intervalAnchor: string | null;
  intervalOriginalMinutes: string | null;
  expression: string;
  at: string;
  originalAt: string | null;
  originalAtDisplay: string | null;
  timezone: string;
};

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function emptySchedule(timezone = browserTimezone()): ScheduleDraft {
  return { kind: "weekdays", days: [1, 2, 3, 4, 5], time: "09:00", everyMinutes: "60", intervalAnchor: null, intervalOriginalMinutes: null, expression: "0 9 * * 1-5", at: "", originalAt: null, originalAtDisplay: null, timezone };
}

// The editor's draft for a saved routine.
export function scheduleDraftOf(routine: Pick<Routine, "schedule_kind" | "schedule_spec" | "timezone">): ScheduleDraft {
  const draft = { ...emptySchedule(routine.timezone), kind: routine.schedule_kind };
  const spec = routine.schedule_spec;
  if (routine.schedule_kind === "weekdays") {
    if (Array.isArray(spec.days)) draft.days = spec.days.filter((d): d is number => typeof d === "number");
    if (typeof spec.time === "string") draft.time = spec.time;
  } else if (routine.schedule_kind === "interval" && typeof spec.every_minutes === "number") {
    draft.everyMinutes = String(spec.every_minutes);
    if (typeof spec.anchor === "string") {
      draft.intervalAnchor = spec.anchor;
      draft.intervalOriginalMinutes = draft.everyMinutes;
    }
  } else if (routine.schedule_kind === "cron" && typeof spec.expression === "string") {
    draft.expression = spec.expression;
  } else if (routine.schedule_kind === "once" && typeof spec.at === "string") {
    draft.at = localInputValue(spec.at, routine.timezone);
    draft.originalAt = spec.at;
    draft.originalAtDisplay = draft.at;
  }
  return draft;
}

// "2026-10-18T09:00" as the routine's zone shows an instant, for <input type="datetime-local">.
export function localInputValue(iso: string, timezone: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date);
  } catch {
    return "";
  }
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}

// The schedule_spec to send for a draft. The server validates it; this only shapes it.
export function scheduleSpec(draft: ScheduleDraft): Record<string, unknown> {
  switch (draft.kind) {
    case "weekdays":
      return { days: [...draft.days].sort((a, b) => a - b), time: draft.time };
    case "interval": {
      const spec: Record<string, unknown> = { every_minutes: Number(draft.everyMinutes) };
      // Keep a saved routine on its original cadence when an unrelated field is edited.
      // Changing the interval intentionally omits the anchor so the backend starts a new one.
      if (draft.intervalAnchor && draft.intervalOriginalMinutes === draft.everyMinutes) {
        spec.anchor = draft.intervalAnchor;
      }
      return spec;
    }
    case "cron":
      return { expression: draft.expression.trim() };
    case "once":
      // Preserve seconds on title-only edits; edited values are local to the routine's timezone.
      return {
        at: draft.originalAt !== null && draft.originalAtDisplay === draft.at
          ? draft.originalAt
          : draft.at,
      };
    default:
      return {};
  }
}

export function schedulePreviewKey(draft: ScheduleDraft): string {
  return JSON.stringify({
    kind: draft.kind,
    spec: scheduleSpec(draft),
    timezone: draft.timezone.trim() || "UTC",
  });
}

export function canSaveSchedulePreview(draft: ScheduleDraft, validPreviewKey: string | null): boolean {
  if (draft.kind === "weekdays" && draft.days.length === 0) return false;
  if (draft.kind === "interval") {
    const minutes = Number(draft.everyMinutes);
    if (!Number.isInteger(minutes) || minutes < 5 || minutes > 10080) return false;
  }
  if (draft.kind === "cron" && !draft.expression.trim()) return false;
  if (draft.kind === "once" && !draft.at.trim()) return false;
  return validPreviewKey === schedulePreviewKey(draft);
}

const STATE_LABEL: Record<RoutineRunState, string> = {
  queued: "Queued",
  running: "Running",
  needs_input: "Needs your approval",
  completed: "Completed",
  failed: "Failed",
  missed: "Missed",
  skipped: "Skipped",
  interrupted: "Interrupted",
};

export function runStateLabel(state: string): string {
  return STATE_LABEL[state as RoutineRunState] ?? state;
}

export function runStateTone(state: string): "ok" | "warn" | "bad" | "muted" | "busy" {
  if (state === "completed") return "ok";
  if (state === "needs_input") return "warn";
  if (state === "failed" || state === "missed" || state === "interrupted") return "bad";
  if (state === "running" || state === "queued") return "busy";
  return "muted";
}

const TRIGGER_LABEL: Record<RoutineRun["trigger"], string> = {
  schedule: "Scheduled",
  manual: "Run now",
  webhook: "Webhook",
  retry: "Retry",
};

export function triggerLabel(trigger: string): string {
  return TRIGGER_LABEL[trigger as RoutineRun["trigger"]] ?? trigger;
}

export function isActiveRun(run: Pick<RoutineRun, "state"> | null | undefined): boolean {
  return !!run && (run.state === "queued" || run.state === "running" || run.state === "needs_input");
}

export function formatWhen(iso: string | null | undefined, timezone?: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: timezone, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

export function formatSchedulePreviewWhen(iso: string | null | undefined, timezone: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  try {
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: timezone, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "shortOffset",
    }).format(date);
  } catch {
    return formatWhen(iso, timezone);
  }
}

export function nextRunText(routine: Pick<Routine, "paused" | "next_run_at" | "schedule_kind" | "timezone">): string {
  if (routine.paused) return "Paused";
  if (routine.schedule_kind === "manual") return "Runs only by hand or webhook";
  if (!routine.next_run_at) return "No upcoming run";
  return `Next: ${formatWhen(routine.next_run_at, routine.timezone)}`;
}

// The full address of a webhook path ("/hooks/12") on the configured backend.
// A page served by the backend itself is built with a relative API path ("/api/v1"),
// so the address it was loaded from is the backend's address.
export function webhookUrl(
  path: string,
  apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1",
  origin = typeof window === "undefined" ? "" : window.location.origin,
): string {
  const base = apiBase.replace(/\/api\/v1\/?$/, "");
  return (/^https?:\/\//.test(base) ? base : origin + base) + path;
}

export function curlExample(url: string, secret: string): string {
  return `curl -X POST '${url}' \\\n  -H 'Authorization: Bearer ${secret}' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"event": "test"}'`;
}

export function badgeText(unread: number): string | null {
  if (unread <= 0) return null;
  return unread > 99 ? "99+" : String(unread);
}
