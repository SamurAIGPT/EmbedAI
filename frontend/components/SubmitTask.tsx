"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, PermissionAsk, Schedule, Task } from "@/lib/api";
import { Button, Card, ErrorText } from "./Card";

const POLL_INTERVAL_MS = 3000;
const HISTORY_REFRESH_INTERVAL_MS = 30_000;
const ACTIVE_STATUSES: Task["status"][] = [
  "queued",
  "starting",
  "pending",
  "running",
  "waiting_approval",
];

const FREQUENCIES = ["hourly", "daily", "weekdays", "weekly"] as const;
type Frequency = (typeof FREQUENCIES)[number];

const FREQUENCY_LABELS: Record<Frequency, string> = {
  hourly: "Hourly",
  daily: "Daily",
  weekdays: "Weekdays (Mon–Fri)",
  weekly: "Weekly",
};

const WEEKDAYS = [
  { value: 0, label: "Sunday" },
  { value: 1, label: "Monday" },
  { value: 2, label: "Tuesday" },
  { value: 3, label: "Wednesday" },
  { value: 4, label: "Thursday" },
  { value: 5, label: "Friday" },
  { value: 6, label: "Saturday" },
];

// Builds a standard 5-field crontab from the simplified UI inputs above.
function buildCronExpression(frequency: Frequency, time: string, weekday: number): string {
  const [hourStr, minuteStr] = time.split(":");
  const hour = Number(hourStr);
  const minute = Number(minuteStr);

  switch (frequency) {
    case "hourly":
      return `${minute} * * * *`;
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekdays":
      return `${minute} ${hour} * * 1-5`;
    case "weekly":
      return `${minute} ${hour} * * ${weekday}`;
  }
}

function describeSchedule(schedule: Schedule): string {
  const next = new Date(schedule.next_run_at).toLocaleString();
  if (!schedule.is_active) return `Paused · next would be ${next}`;
  return `Next run ${next}`;
}

function statusColor(status: Task["status"]) {
  switch (status) {
    case "succeeded":
      return "text-emerald-600 dark:text-emerald-400";
    case "failed":
      return "text-red-600 dark:text-red-400";
    default:
      return "text-amber-600 dark:text-amber-400";
  }
}

export function SubmitTask({
  userId,
  boxId,
}: {
  userId: string;
  boxId: string | null;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [composer, setComposer] = useState("");
  const [composerBusy, setComposerBusy] = useState(false);
  const [composerError, setComposerError] = useState<string | null>(null);

  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [frequency, setFrequency] = useState<Frequency>("daily");
  const [scheduleTime, setScheduleTime] = useState("08:00");
  const [scheduleWeekday, setScheduleWeekday] = useState(1);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [scheduleBusy, setScheduleBusy] = useState<Record<number, boolean>>({});
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({});
  const [replyBusy, setReplyBusy] = useState<Record<number, boolean>>({});
  const [replyErrors, setReplyErrors] = useState<Record<number, string | null>>({});
  const pollers = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const knownTaskIds = useRef(new Set<number>());
  const listVersion = useRef(0);

  const [pendingAsks, setPendingAsks] = useState<Record<number, PermissionAsk>>({});
  const [askBusy, setAskBusy] = useState<Record<number, boolean>>({});

  const upsertTask = (task: Task) => {
    knownTaskIds.current.add(task.id);
    setTasks((prev) => {
      const next = prev.filter((t) => t.id !== task.id);
      next.unshift(task);
      return next.sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      );
    });
  };

  const stopPolling = (taskId: number) => {
    const handle = pollers.current.get(taskId);
    if (handle) {
      clearInterval(handle);
      pollers.current.delete(taskId);
    }
  };

  const clearPendingAsk = (taskId: number) => {
    setPendingAsks((prev) => {
      if (!(taskId in prev)) return prev;
      const next = { ...prev };
      delete next[taskId];
      return next;
    });
  };

  const fetchPendingAsk = async (taskId: number) => {
    try {
      const ask = await api.getPendingAsk(taskId);
      if (ask && pollers.current.has(taskId)) {
        setPendingAsks((prev) => ({ ...prev, [taskId]: ask }));
      }
    } catch {
      // transient — the next poll tick will retry
    }
  };

  const startPolling = (taskId: number) => {
    if (pollers.current.has(taskId)) return;
    let pending = false;
    const handle = setInterval(async () => {
      if (pending) return;
      pending = true;
      try {
        const updated = await api.getTask(taskId);
        if (pollers.current.get(taskId) !== handle) return;
        upsertTask(updated);
        if (updated.status === "waiting_approval") {
          fetchPendingAsk(taskId);
        } else {
          clearPendingAsk(taskId);
        }
        if (!ACTIVE_STATUSES.includes(updated.status)) {
          stopPolling(taskId);
        }
      } catch {
        // transient — keep polling
      } finally {
        pending = false;
      }
    }, POLL_INTERVAL_MS);
    pollers.current.set(taskId, handle);
  };

  useEffect(() => {
    let cancelled = false;
    let refreshing = false;
    let replaceHistory = true;
    knownTaskIds.current.clear();

    const refresh = async () => {
      if (cancelled || refreshing || document.visibilityState !== "visible") return;
      refreshing = true;
      const version = listVersion.current;
      try {
        try {
          const history = await api.listTasks(userId);
          if (cancelled) return;
          if (version === listVersion.current) {
            if (replaceHistory) {
              const retainedIds = new Set(knownTaskIds.current);
              setTasks((prev) => prev.filter((task) => retainedIds.has(task.id)));
              replaceHistory = false;
            }
            // Known tasks are updated by their task poller or local mutation;
            // an older history snapshot must not replace those results.
            history.slice().reverse().forEach((task) => {
              if (knownTaskIds.current.has(task.id)) return;
              upsertTask(task);
              if (ACTIVE_STATUSES.includes(task.status)) {
                startPolling(task.id);
                if (task.status === "waiting_approval") fetchPendingAsk(task.id);
              }
            });
          }
        } catch {
          // transient — retry the history on the next refresh
        }
        if (cancelled) return;
        try {
          const updated = await api.listSchedules(userId);
          if (!cancelled && version === listVersion.current) setSchedules(updated);
        } catch {
          // transient — retain the schedule list and retry
        }
      } finally {
        refreshing = false;
      }
    };
    const refreshVisible = () => {
      void refresh();
    };
    void refresh();
    const handle = setInterval(refreshVisible, HISTORY_REFRESH_INTERVAL_MS);
    document.addEventListener("visibilitychange", refreshVisible);

    return () => {
      cancelled = true;
      clearInterval(handle);
      document.removeEventListener("visibilitychange", refreshVisible);
      pollers.current.forEach((poller) => clearInterval(poller));
      pollers.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const submitNew = async () => {
    if (!boxId || !composer.trim()) return;
    setComposerError(null);
    listVersion.current += 1;
    setComposerBusy(true);
    try {
      if (scheduleEnabled) {
        const cron = buildCronExpression(frequency, scheduleTime, scheduleWeekday);
        const schedule = await api.createSchedule(
          userId,
          boxId,
          composer.trim(),
          cron,
          timezone
        );
        setSchedules((prev) => [schedule, ...prev]);
        setComposer("");
        setScheduleEnabled(false);
      } else {
        const task = await api.submitTask(userId, boxId, composer.trim());
        upsertTask(task);
        setExpandedId(task.id);
        setComposer("");
        if (ACTIVE_STATUSES.includes(task.status)) startPolling(task.id);
      }
    } catch (err) {
      setComposerError(
        err instanceof ApiError
          ? err.message
          : `Could not ${scheduleEnabled ? "create schedule" : "submit task"}.`
      );
    } finally {
      listVersion.current += 1;
      setComposerBusy(false);
    }
  };

  const toggleSchedule = async (schedule: Schedule) => {
    listVersion.current += 1;
    setScheduleBusy((prev) => ({ ...prev, [schedule.id]: true }));
    try {
      const updated = schedule.is_active
        ? await api.pauseSchedule(userId, schedule.id)
        : await api.resumeSchedule(userId, schedule.id);
      setSchedules((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch {
      // transient — leave state as-is, user can retry
    } finally {
      listVersion.current += 1;
      setScheduleBusy((prev) => ({ ...prev, [schedule.id]: false }));
    }
  };

  const removeSchedule = async (schedule: Schedule) => {
    listVersion.current += 1;
    setScheduleBusy((prev) => ({ ...prev, [schedule.id]: true }));
    try {
      await api.deleteSchedule(userId, schedule.id);
      setSchedules((prev) => prev.filter((s) => s.id !== schedule.id));
    } catch {
      setScheduleBusy((prev) => ({ ...prev, [schedule.id]: false }));
    } finally {
      listVersion.current += 1;
    }
  };

  const submitReply = async (parent: Task) => {
    const text = (replyDrafts[parent.id] ?? "").trim();
    if (!boxId || !text) return;
    setReplyErrors((prev) => ({ ...prev, [parent.id]: null }));
    listVersion.current += 1;
    setReplyBusy((prev) => ({ ...prev, [parent.id]: true }));
    try {
      const task = await api.submitTask(userId, boxId, text, parent.id);
      upsertTask(task);
      setExpandedId(task.id);
      setReplyDrafts((prev) => ({ ...prev, [parent.id]: "" }));
      if (ACTIVE_STATUSES.includes(task.status)) startPolling(task.id);
    } catch (err) {
      setReplyErrors((prev) => ({
        ...prev,
        [parent.id]: err instanceof ApiError ? err.message : "Could not send reply.",
      }));
    } finally {
      listVersion.current += 1;
      setReplyBusy((prev) => ({ ...prev, [parent.id]: false }));
    }
  };

  const answerAsk = async (task: Task, decision: "allow" | "deny", always: boolean) => {
    const ask = pendingAsks[task.id];
    if (!ask) return;
    setAskBusy((prev) => ({ ...prev, [ask.id]: true }));
    try {
      await api.answerAsk(ask.id, decision, always);
      clearPendingAsk(task.id);
      const updated = await api.getTask(task.id);
      upsertTask(updated);
      if (ACTIVE_STATUSES.includes(updated.status)) startPolling(task.id);
    } catch {
      // transient — leave the ask in place so the user can retry
    } finally {
      setAskBusy((prev) => ({ ...prev, [ask.id]: false }));
    }
  };

  return (
    <Card step={4} title="Submit a task" disabled={!boxId}>
      <div className="space-y-3">
        <textarea
          value={composer}
          onChange={(e) => setComposer(e.target.value)}
          placeholder='e.g. "create a webpage that says hello"'
          rows={3}
          className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
        />
        <label className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
          <input
            type="checkbox"
            checked={scheduleEnabled}
            onChange={(e) => setScheduleEnabled(e.target.checked)}
          />
          Repeat this on a schedule
        </label>

        {scheduleEnabled && (
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
            <select
              value={frequency}
              onChange={(e) => setFrequency(e.target.value as Frequency)}
              className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            >
              {FREQUENCIES.map((f) => (
                <option key={f} value={f}>
                  {FREQUENCY_LABELS[f]}
                </option>
              ))}
            </select>

            {frequency === "weekly" && (
              <select
                value={scheduleWeekday}
                onChange={(e) => setScheduleWeekday(Number(e.target.value))}
                className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              >
                {WEEKDAYS.map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
            )}

            {frequency !== "hourly" && (
              <input
                type="time"
                value={scheduleTime}
                onChange={(e) => setScheduleTime(e.target.value)}
                className="rounded-md border border-zinc-300 px-2 py-1.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              />
            )}

            <span className="text-xs text-zinc-500">{timezone}</span>
          </div>
        )}

        <Button onClick={submitNew} disabled={composerBusy || !composer.trim()}>
          {composerBusy
            ? scheduleEnabled
              ? "Creating…"
              : "Submitting…"
            : scheduleEnabled
              ? "Create schedule"
              : "Submit task"}
        </Button>
        <ErrorText message={composerError} />

        {schedules.length > 0 && (
          <ul className="space-y-2 pt-2">
            {schedules.map((schedule) => (
              <li
                key={schedule.id}
                className="rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-zinc-700 dark:text-zinc-300">
                      {schedule.prompt_text}
                    </p>
                    <p className="text-xs text-zinc-500">
                      {schedule.cron_expression} ({schedule.timezone}) ·{" "}
                      {describeSchedule(schedule)}
                    </p>
                    {schedule.paused_reason && (
                      <p className="text-xs text-red-600 dark:text-red-400">
                        Paused: {schedule.paused_reason}
                      </p>
                    )}
                    {!schedule.paused_reason && schedule.last_error && (
                      <p className="text-xs text-red-600 dark:text-red-400">
                        {schedule.last_error}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <button
                      onClick={() => toggleSchedule(schedule)}
                      disabled={scheduleBusy[schedule.id]}
                      className="text-xs text-zinc-500 underline disabled:opacity-50"
                    >
                      {schedule.is_active ? "Pause" : "Resume"}
                    </button>
                    <button
                      onClick={() => removeSchedule(schedule)}
                      disabled={scheduleBusy[schedule.id]}
                      className="text-xs text-red-600 underline disabled:opacity-50 dark:text-red-400"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        {tasks.length > 0 && (
          <ul className="space-y-2 pt-2">
            {tasks.map((task) => {
              const expanded = expandedId === task.id;
              const active = ACTIVE_STATUSES.includes(task.status);
              const ask =
                task.status === "waiting_approval" ? pendingAsks[task.id] : undefined;
              return (
                <li
                  key={task.id}
                  className="rounded-md border border-zinc-200 dark:border-zinc-800"
                >
                  <button
                    onClick={() => setExpandedId(expanded ? null : task.id)}
                    className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm"
                  >
                    <span className="truncate text-zinc-700 dark:text-zinc-300">
                      #{task.id} {task.prompt_text}
                    </span>
                    <span className={`shrink-0 text-xs ${statusColor(task.status)}`}>
                      {task.status}
                    </span>
                  </button>

                  {expanded && (
                    <div className="border-t border-zinc-200 p-3 text-sm dark:border-zinc-800">
                      {task.result && (
                        <pre className="whitespace-pre-wrap break-words text-xs text-zinc-700 dark:text-zinc-300">
                          {task.result}
                        </pre>
                      )}
                      {task.error && (
                        <pre className="whitespace-pre-wrap break-words text-xs text-red-600 dark:text-red-400">
                          {task.error}
                        </pre>
                      )}
                      {task.status === "waiting_approval" && (
                        <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950">
                          <p className="text-xs text-amber-800 dark:text-amber-300">
                            {ask
                              ? `Wants to use: ${ask.tool ?? ask.connector ?? ask.bucket} (bucket: ${ask.bucket}). Allow it?`
                              : "Waiting on a permission decision…"}
                          </p>
                          {ask && (
                            <div className="flex flex-wrap gap-2">
                              <Button
                                onClick={() => answerAsk(task, "allow", false)}
                                disabled={askBusy[ask.id]}
                              >
                                Allow once
                              </Button>
                              <Button
                                onClick={() => answerAsk(task, "allow", true)}
                                disabled={askBusy[ask.id]}
                              >
                                Always allow
                              </Button>
                              <button
                                onClick={() => answerAsk(task, "deny", false)}
                                disabled={askBusy[ask.id]}
                                className="rounded-md border border-red-300 px-4 py-2 text-sm font-medium text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-800 dark:text-red-400 dark:hover:bg-red-950"
                              >
                                Deny
                              </button>
                            </div>
                          )}
                        </div>
                      )}

                      {active && task.status !== "waiting_approval" && (
                        <p className="text-xs text-zinc-500">Working…</p>
                      )}

                      {!active && (
                        <div className="mt-3 space-y-2">
                          <textarea
                            value={replyDrafts[task.id] ?? ""}
                            onChange={(e) =>
                              setReplyDrafts((prev) => ({ ...prev, [task.id]: e.target.value }))
                            }
                            placeholder="Reply in this conversation…"
                            rows={2}
                            className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                          />
                          <Button
                            onClick={() => submitReply(task)}
                            disabled={
                              replyBusy[task.id] || !(replyDrafts[task.id] ?? "").trim()
                            }
                          >
                            {replyBusy[task.id] ? "Sending…" : "Reply"}
                          </Button>
                          <ErrorText message={replyErrors[task.id] ?? null} />
                        </div>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
