"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, LocalStatus, RunEvent, Task } from "@/lib/api";
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

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

// Runs Claude through the user's own signed-in CLI on this machine. Chat only:
// Claude has no tools here, so it can answer but cannot touch files or run
// commands. Hidden entirely unless the backend enables it.
//
// The answer appears as Claude writes it (server-sent events). The stream is
// only a view: the outcome shown for a run always comes from the saved task,
// which is read when the stream ends for any reason, so a dropped connection
// can never show as a success.
export function LocalClaude() {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [liveText, setLiveText] = useState<Record<number, string>>({});
  const [prompt, setPrompt] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollers = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const followers = useRef<Map<number, AbortController>>(new Map());

  const upsert = (task: Task) =>
    setTasks((prev) => [task, ...prev.filter((t) => t.id !== task.id)].sort((a, b) => b.id - a.id));

  const clearLive = (taskId: number) =>
    setLiveText((prev) => {
      if (!(taskId in prev)) return prev;
      const next = { ...prev };
      delete next[taskId];
      return next;
    });

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
        if (!ACTIVE_STATUSES.includes(updated.status)) {
          stopPolling(taskId);
          clearLive(taskId);
        }
      } catch {
        // transient — keep polling
      }
    }, POLL_INTERVAL_MS);
    pollers.current.set(taskId, handle);
  };

  const applyEvent = (taskId: number, event: RunEvent) => {
    if (event.type === "text.delta") {
      const text = asText(event.data.text);
      const replace = event.data.mode === "replace";
      setLiveText((prev) => ({ ...prev, [taskId]: replace ? text : (prev[taskId] ?? "") + text }));
    } else if (event.type === "turn.completed" || event.type === "turn.failed") {
      // The server's final text (the partial text, for a failure). It is shown
      // until the saved task is read right after the stream closes.
      const finalText = asText(event.data.result);
      if (finalText) setLiveText((prev) => ({ ...prev, [taskId]: finalText }));
    }
    // turn.started and runtime.status need no display: a stream that ends
    // early is handled by reading the task and polling.
  };

  // Shows the answer as it arrives. Whatever ends the stream (finished, lagged,
  // dropped, server restarted), the saved task decides what is shown next.
  const follow = (taskId: number) => {
    if (followers.current.has(taskId)) return;
    const controller = new AbortController();
    followers.current.set(taskId, controller);
    api
      .streamLocalTask(taskId, (event) => applyEvent(taskId, event), controller.signal)
      .catch(() => {
        // dropped or refused: fall through to reading the task
      })
      .finally(async () => {
        // Only drop our own entry: after an unmount/remount a newer stream may
        // already be registered under this id.
        if (followers.current.get(taskId) === controller) followers.current.delete(taskId);
        if (controller.signal.aborted) return;
        try {
          const updated = await api.getLocalTask(taskId);
          upsert(updated);
          if (ACTIVE_STATUSES.includes(updated.status)) startPolling(taskId);
          else clearLive(taskId);
        } catch {
          startPolling(taskId);
        }
      });
  };

  useEffect(() => {
    let cancelled = false;
    const pollerMap = pollers.current;
    const followerMap = followers.current;
    (async () => {
      try {
        const next = await api.getLocalStatus();
        if (cancelled) return;
        setStatus(next);
        if (!next.available) return;
        const history = await api.listLocalTasks(LOCAL_OWNER_ID);
        if (cancelled) return;
        setTasks(history);
        history.filter((t) => ACTIVE_STATUSES.includes(t.status)).forEach((t) => follow(t.id));
      } catch {
        // backend unreachable or older than this feature: stay hidden
      }
    })();
    return () => {
      cancelled = true;
      pollerMap.forEach((handle) => clearInterval(handle));
      pollerMap.clear();
      followerMap.forEach((controller) => controller.abort());
      followerMap.clear();
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
      if (ACTIVE_STATUSES.includes(task.status)) follow(task.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the local run.");
    } finally {
      setBusy(false);
    }
  };

  const renderBody = (task: Task) => {
    const active = ACTIVE_STATUSES.includes(task.status);
    const live = liveText[task.id];
    const answer = active && live !== undefined ? live : task.result;

    if (active && !answer) {
      return <p className="text-xs text-zinc-500">Waiting for Claude…</p>;
    }
    if (task.status === "succeeded" && !answer?.trim()) {
      return <p className="text-xs text-zinc-500">Claude returned an empty response.</p>;
    }
    return (
      <>
        {answer && (
          <pre className="whitespace-pre-wrap break-words text-xs text-zinc-700 dark:text-zinc-300">
            {answer}
            {active && <span className="animate-pulse">▍</span>}
          </pre>
        )}
        {task.status === "failed" && answer && (
          <p className="mt-2 text-xs text-zinc-500">The run stopped before finishing. This is the partial answer.</p>
        )}
        {task.error && (
          <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-red-600 dark:text-red-400">
            {task.error}
          </pre>
        )}
        {task.status === "failed" && !task.error && !answer && (
          <p className="text-xs text-red-600 dark:text-red-400">The run failed.</p>
        )}
      </>
    );
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
                        {renderBody(task)}
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
