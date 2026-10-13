"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, LocalStatus, Task } from "@/lib/api";
import { Button, ErrorText } from "./Card";

// Local runs belong to whoever uses this machine's browser, not to a guest or
// Telegram identity, so they use one fixed owner id.
const LOCAL_OWNER_ID = "local-owner";
const POLL_INTERVAL_MS = 2000;
const ACTIVE_STATUSES: Task["status"][] = ["queued", "starting", "pending", "running"];

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

// Runs Claude through the user's own signed-in CLI on this machine. Chat only:
// Claude has no tools here, so it can answer but cannot touch files or run
// commands. Hidden entirely unless the backend enables it.
export function LocalClaude() {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [prompt, setPrompt] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollers = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());

  const upsert = (task: Task) =>
    setTasks((prev) => [task, ...prev.filter((t) => t.id !== task.id)].sort((a, b) => b.id - a.id));

  const stopPolling = (taskId: number) => {
    const handle = pollers.current.get(taskId);
    if (handle) clearInterval(handle);
    pollers.current.delete(taskId);
  };

  const startPolling = (taskId: number) => {
    if (pollers.current.has(taskId)) return;
    const handle = setInterval(async () => {
      try {
        const updated = await api.getLocalTask(taskId);
        upsert(updated);
        if (!ACTIVE_STATUSES.includes(updated.status)) stopPolling(taskId);
      } catch {
        // transient — keep polling
      }
    }, POLL_INTERVAL_MS);
    pollers.current.set(taskId, handle);
  };

  useEffect(() => {
    let cancelled = false;
    const pollerMap = pollers.current;
    (async () => {
      try {
        const next = await api.getLocalStatus();
        if (cancelled) return;
        setStatus(next);
        if (!next.available) return;
        const history = await api.listLocalTasks(LOCAL_OWNER_ID);
        if (cancelled) return;
        setTasks(history);
        history.filter((t) => ACTIVE_STATUSES.includes(t.status)).forEach((t) => startPolling(t.id));
      } catch {
        // backend unreachable or older than this feature: stay hidden
      }
    })();
    return () => {
      cancelled = true;
      pollerMap.forEach((handle) => clearInterval(handle));
      pollerMap.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!status || !status.enabled) return null;

  const submit = async () => {
    const text = prompt.trim();
    if (!text) return;
    setError(null);
    setBusy(true);
    try {
      const task = await api.createLocalTask(LOCAL_OWNER_ID, text);
      upsert(task);
      setExpandedId(task.id);
      setPrompt("");
      if (ACTIVE_STATUSES.includes(task.status)) startPolling(task.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the local run.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Local Claude</h2>
        {status.cli_version && (
          <span className="text-xs text-zinc-500">Claude Code {status.cli_version}</span>
        )}
      </div>

      {!status.available ? (
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          Local Claude is not available: {status.reason ?? "unknown reason"}
        </p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-zinc-500">
            Runs on this computer with your own signed-in Claude CLI. Chat only: Claude cannot use
            tools, read files or run commands here yet.
          </p>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Ask Claude something"
            rows={3}
            className="w-full rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
          />
          <Button onClick={submit} disabled={busy || !prompt.trim()}>
            {busy ? "Starting…" : "Ask Claude"}
          </Button>
          <ErrorText message={error} />

          {tasks.length > 0 && (
            <ul className="space-y-2 pt-2">
              {tasks.map((task) => {
                const expanded = expandedId === task.id;
                return (
                  <li
                    key={task.id}
                    className="rounded-md border border-zinc-200 dark:border-zinc-800"
                  >
                    <button
                      onClick={() => setExpandedId(expanded ? null : task.id)}
                      className="flex w-full cursor-pointer items-center justify-between gap-3 px-3 py-2 text-left text-sm"
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
                        {!task.result && !task.error && (
                          <p className="text-xs text-zinc-500">Waiting for Claude…</p>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
