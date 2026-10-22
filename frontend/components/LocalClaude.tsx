"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, LocalApproval, LocalStatus, RunEvent, Task } from "@/lib/api";
import { Button, ErrorText } from "./Card";

// Local runs belong to whoever uses this machine's browser, not to a guest or
// Telegram identity, so they use one fixed owner id.
const LOCAL_OWNER_ID = "local-owner";
const POLL_INTERVAL_MS = 2000;
const ACTIVE_STATUSES: Task["status"][] = ["queued", "starting", "pending", "running"];

function statusColor(status: Task["status"]) {
  switch (status) {
    case "succeeded":
      return "text-success";
    case "failed":
      return "text-danger";
    default:
      return "text-warning";
  }
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

// One tool call in a run, as shown in the timeline.
type ToolStep = {
  id: string;
  tool: string;
  summary: string;
  state: "running" | "done" | "error";
  preview: string;
};

// A value is shown exactly as it will be used, so a person approves what runs:
// strings verbatim (a command, file contents), anything else as JSON.
function formatInput(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function ApprovalCard({
  approval,
  disabled,
  onAnswer,
}: {
  approval: LocalApproval;
  disabled: boolean;
  onAnswer: (decision: "allow" | "deny") => void;
}) {
  return (
    <div className="space-y-2 rounded-md border border-warning/60 bg-warning/10 p-3">
      <p className="text-xs font-medium text-ink">
        Claude wants to use <span className="font-mono">{approval.tool}</span>. It will not run
        until you allow it.
      </p>
      <dl className="space-y-1">
        {Object.entries(approval.input).map(([key, value]) => (
          <div key={key}>
            <dt className="text-xs text-muted">{key}</dt>
            <dd>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded bg-code p-2 font-mono text-xs text-ink">
                {formatInput(value)}
              </pre>
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex gap-2">
        <Button onClick={() => onAnswer("allow")} disabled={disabled}>
          Allow
        </Button>
        <Button onClick={() => onAnswer("deny")} disabled={disabled}>
          Deny
        </Button>
      </div>
    </div>
  );
}

// Runs Claude through the user's own signed-in CLI on this machine. With tools
// off (the default) it is chat only. With tools on, Claude can read and write
// files in its workspace and run commands, and every call it would ask about
// waits here for Allow or Deny. Hidden entirely unless the backend enables it.
//
// The answer appears as Claude writes it (server-sent events). The stream is
// only a view: the outcome shown for a run always comes from the saved task,
// which is read when the stream ends for any reason, so a dropped connection
// can never show as a success.
export function LocalClaude() {
  const [status, setStatus] = useState<LocalStatus | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [liveText, setLiveText] = useState<Record<number, string>>({});
  const [steps, setSteps] = useState<Record<number, ToolStep[]>>({});
  const [approvals, setApprovals] = useState<Record<number, LocalApproval[]>>({});
  const [answering, setAnswering] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollers = useRef<Map<number, ReturnType<typeof setInterval>>>(new Map());
  const followers = useRef<Map<number, AbortController>>(new Map());

  const upsert = (task: Task) =>
    setTasks((prev) => [task, ...prev.filter((t) => t.id !== task.id)].sort((a, b) => b.id - a.id));

  const clearLive = (taskId: number) => {
    setLiveText((prev) => {
      if (!(taskId in prev)) return prev;
      const next = { ...prev };
      delete next[taskId];
      return next;
    });
    // A finished run waits for nothing. The timeline stays for this page visit.
    setApprovals((prev) => {
      if (!(taskId in prev)) return prev;
      const next = { ...prev };
      delete next[taskId];
      return next;
    });
  };

  // The endpoint is the source of truth for what is waiting; events only say
  // that it is worth asking again.
  const refreshApprovals = async (taskId: number) => {
    try {
      const pending = await api.listLocalApprovals(taskId);
      setApprovals((prev) => ({ ...prev, [taskId]: pending }));
    } catch {
      // transient, or an older backend without approvals
    }
  };

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
        } else {
          void refreshApprovals(taskId);
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
    } else if (event.type === "tool.started") {
      const id = asText(event.data.tool_use_id);
      if (!id) return;
      const step: ToolStep = {
        id,
        tool: asText(event.data.tool) || "tool",
        summary: asText(event.data.summary),
        state: "running",
        preview: "",
      };
      // A replayed event may already be known: keep one entry per call.
      setSteps((prev) => {
        const list = prev[taskId] ?? [];
        return list.some((s) => s.id === id) ? prev : { ...prev, [taskId]: [...list, step] };
      });
    } else if (event.type === "tool.completed") {
      const id = asText(event.data.tool_use_id);
      const failed = event.data.is_error === true;
      const preview = asText(event.data.preview);
      setSteps((prev) => ({
        ...prev,
        [taskId]: (prev[taskId] ?? []).map((s) =>
          s.id === id ? { ...s, state: failed ? "error" : "done", preview } : s
        ),
      }));
    } else if (event.type === "approval.requested" || event.type === "approval.resolved") {
      void refreshApprovals(taskId);
    }
    // turn.started and runtime.status need no display: a stream that ends
    // early is handled by reading the task and polling.
  };

  const answerApproval = async (
    taskId: number,
    approvalId: string,
    decision: "allow" | "deny"
  ) => {
    setApprovalError(null);
    setAnswering(approvalId);
    try {
      await api.answerLocalApproval(taskId, approvalId, decision);
    } catch (err) {
      // 404: it was answered elsewhere, expired, or the run ended. Not an error
      // worth showing; the refresh below removes the card.
      if (!(err instanceof ApiError && err.status === 404)) {
        setApprovalError(err instanceof ApiError ? err.message : "Could not send your answer.");
      }
    } finally {
      setAnswering(null);
      await refreshApprovals(taskId);
    }
  };

  // Shows the answer as it arrives. Whatever ends the stream (finished, lagged,
  // dropped, server restarted), the saved task decides what is shown next.
  const follow = (taskId: number) => {
    if (followers.current.has(taskId)) return;
    const controller = new AbortController();
    followers.current.set(taskId, controller);
    // A page opened while Claude is already waiting must show the question.
    void refreshApprovals(taskId);
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

    const taskSteps = steps[task.id] ?? [];
    const pending = approvals[task.id] ?? [];

    const timeline = taskSteps.length > 0 && (
      <ul className="mb-3 space-y-1">
        {taskSteps.map((step) => (
          <li key={step.id} className="text-xs" title={step.preview || undefined}>
            <span
              className={
                step.state === "error"
                  ? "text-danger"
                  : step.state === "done"
                    ? "text-success"
                    : "text-warning"
              }
            >
              {step.state === "running" ? "…" : step.state === "done" ? "✓" : "✗"}
            </span>{" "}
            <span className="font-mono text-ink">{step.tool}</span>{" "}
            <span className="break-all text-muted">{step.summary}</span>
          </li>
        ))}
      </ul>
    );

    const questions = pending.length > 0 && (
      <div className="mb-3 space-y-2">
        {pending.map((approval) => (
          <ApprovalCard
            key={approval.id}
            approval={approval}
            disabled={answering === approval.id}
            onAnswer={(decision) => answerApproval(task.id, approval.id, decision)}
          />
        ))}
        <ErrorText message={approvalError} />
      </div>
    );

    if (active && !answer) {
      return (
        <>
          {timeline}
          {questions}
          {pending.length === 0 && <p className="text-xs text-muted">Waiting for Claude…</p>}
        </>
      );
    }
    if (task.status === "succeeded" && !answer?.trim()) {
      return (
        <>
          {timeline}
          <p className="text-xs text-muted">Claude returned an empty response.</p>
        </>
      );
    }
    return (
      <>
        {timeline}
        {questions}
        {answer && (
          <pre className="whitespace-pre-wrap break-words text-xs text-ink">
            {answer}
            {active && <span className="animate-pulse">▍</span>}
          </pre>
        )}
        {task.status === "failed" && answer && (
          <p className="mt-2 text-xs text-muted">The run stopped before finishing. This is the partial answer.</p>
        )}
        {task.error && (
          <pre className="mt-2 whitespace-pre-wrap break-words text-xs text-danger">
            {task.error}
          </pre>
        )}
        {task.status === "failed" && !task.error && !answer && (
          <p className="text-xs text-danger">The run failed.</p>
        )}
      </>
    );
  };

  return (
    <section className="rounded-lg border border-line p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="text-base font-semibold">Local Claude</h2>
        {status.cli_version && (
          <span className="text-xs text-muted">Claude Code {status.cli_version}</span>
        )}
      </div>

      {!status.available ? (
        <p className="text-sm text-muted">
          Local Claude is not available: {status.reason ?? "unknown reason"}
        </p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-muted">
            {status.tools_enabled
              ? "Runs on this computer with your own signed-in Claude CLI. Claude can read and write files in its workspace folder and run commands, but it asks first: nothing it is unsure about runs until you press Allow."
              : "Runs on this computer with your own signed-in Claude CLI. Chat only: Claude cannot use tools, read files or run commands here. Set LOCAL_TOOLS_ENABLED=true in backend/.env to let it ask to."}
          </p>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Ask Claude something"
            rows={3}
            className="w-full rounded-md border border-line px-3 py-2 text-sm"
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
                    className="rounded-md border border-line"
                  >
                    <button
                      onClick={() => setExpandedId(expanded ? null : task.id)}
                      className="flex w-full cursor-pointer items-center justify-between gap-3 px-3 py-2 text-left text-sm"
                    >
                      <span className="truncate text-ink">
                        #{task.id} {task.prompt_text}
                      </span>
                      <span className={`shrink-0 text-xs ${statusColor(task.status)}`}>
                        {task.status}
                      </span>
                    </button>

                    {expanded && (
                      <div className="border-t border-line p-3 text-sm">
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
