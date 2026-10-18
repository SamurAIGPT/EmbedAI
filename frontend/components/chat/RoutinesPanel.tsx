"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type Agent,
  type ApprovalMode,
  type Routine,
  type RoutineInput,
  type RoutineRun,
  type SchedulePreview,
} from "@/lib/api";
import { activeAgents, APPROVAL_MODES } from "@/lib/agents";
import {
  curlExample,
  DAY_LABELS,
  emptySchedule,
  formatWhen,
  isActiveRun,
  nextRunText,
  runStateLabel,
  runStateTone,
  SCHEDULE_KINDS,
  scheduleDraftOf,
  scheduleSpec,
  triggerLabel,
  webhookUrl,
  type ScheduleDraft,
} from "@/lib/routines";

const REFRESH_MS = 5_000;
const input = "w-full rounded-md border border-zinc-300 bg-transparent px-2 py-1.5 text-sm dark:border-zinc-700";
const button = "cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-900";

const TONE: Record<ReturnType<typeof runStateTone>, string> = {
  ok: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  bad: "text-red-700 dark:text-red-400",
  busy: "text-sky-700 dark:text-sky-400",
  muted: "text-zinc-500",
};

type Draft = {
  id: number | null;
  name: string;
  agentId: number | null;
  prompt: string;
  schedule: ScheduleDraft;
  runLimit: string;
  approvalMode: ApprovalMode;
  approvalWait: string;
  catchUp: string;
};

function newDraft(agents: Agent[]): Draft {
  return {
    id: null, name: "", agentId: agents[0]?.id ?? null, prompt: "", schedule: emptySchedule(),
    runLimit: "30", approvalMode: "ask", approvalWait: "30", catchUp: "60",
  };
}

function draftOf(routine: Routine): Draft {
  return {
    id: routine.id, name: routine.name, agentId: routine.agent_id, prompt: routine.prompt,
    schedule: scheduleDraftOf(routine), runLimit: String(routine.run_limit_minutes),
    approvalMode: routine.approval_mode, approvalWait: String(routine.approval_timeout_minutes),
    catchUp: String(routine.catch_up_minutes),
  };
}

function inputOf(draft: Draft): RoutineInput {
  return {
    agent_id: draft.agentId ?? 0,
    name: draft.name.trim(),
    prompt: draft.prompt.trim(),
    schedule_kind: draft.schedule.kind,
    schedule_spec: scheduleSpec(draft.schedule),
    timezone: draft.schedule.timezone.trim() || "UTC",
    run_limit_minutes: Number(draft.runLimit),
    approval_mode: draft.approvalMode,
    approval_timeout_minutes: Number(draft.approvalWait),
    catch_up_minutes: Number(draft.catchUp),
  };
}

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message || fallback;
  return "Could not reach the backend.";
}

function State({ state }: { state: string }) {
  return <span className={`text-xs font-medium ${TONE[runStateTone(state)]}`}>{runStateLabel(state)}</span>;
}

// The schedule part of the editor, with the server's own summary and next three dates.
function ScheduleFields({ value, onChange }: { value: ScheduleDraft; onChange: (next: ScheduleDraft) => void }) {
  const [preview, setPreview] = useState<SchedulePreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const set = (patch: Partial<ScheduleDraft>) => onChange({ ...value, ...patch });

  useEffect(() => {
    let live = true;
    const timer = setTimeout(() => {
      api.previewSchedule(value.kind, scheduleSpec(value), value.timezone.trim() || "UTC")
        .then((result) => { if (live) { setPreview(result); setProblem(null); } })
        .catch((err) => { if (live) { setPreview(null); setProblem(errorText(err, "This schedule is not valid.")); } });
    }, 300);
    return () => { live = false; clearTimeout(timer); };
  }, [value]);

  return (
    <fieldset className="space-y-2 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
      <legend className="px-1 text-xs font-medium text-zinc-500">Schedule</legend>
      <select aria-label="Schedule kind" value={value.kind} onChange={(e) => set({ kind: e.target.value as ScheduleDraft["kind"] })} className={input}>
        {SCHEDULE_KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
      </select>
      {value.kind === "weekdays" && (
        <div className="flex flex-wrap items-center gap-1">
          {DAY_LABELS.map((label, day) => {
            const on = value.days.includes(day);
            return (
              <button key={label} type="button" aria-pressed={on} onClick={() => set({ days: on ? value.days.filter((d) => d !== day) : [...value.days, day] })}
                className={`cursor-pointer rounded px-2 py-1 text-xs ${on ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900" : "border border-zinc-300 dark:border-zinc-700"}`}>
                {label}
              </button>
            );
          })}
          <input aria-label="Time" type="time" value={value.time} onChange={(e) => set({ time: e.target.value })} className="ml-2 rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-sm dark:border-zinc-700" />
        </div>
      )}
      {value.kind === "interval" && (
        <label className="flex items-center gap-2 text-sm">Every
          <input aria-label="Minutes between runs" type="number" min={5} max={10080} value={value.everyMinutes} onChange={(e) => set({ everyMinutes: e.target.value })} className="w-24 rounded-md border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700" />
          minutes
        </label>
      )}
      {value.kind === "cron" && (
        <input aria-label="Cron expression" value={value.expression} onChange={(e) => set({ expression: e.target.value })} placeholder="minute hour day month weekday, e.g. 0 9 * * 1-5" spellCheck={false} className={`${input} font-mono`} />
      )}
      {value.kind === "once" && (
        <input aria-label="Date and time" type="datetime-local" value={value.at} onChange={(e) => set({ at: e.target.value })} className={input} />
      )}
      {value.kind !== "manual" && value.kind !== "interval" && (
        <label className="block text-xs text-zinc-500">Time zone
          <input aria-label="Time zone" value={value.timezone} onChange={(e) => set({ timezone: e.target.value })} placeholder="Europe/Berlin" spellCheck={false} className={input} />
        </label>
      )}
      {problem ? (
        <p role="alert" className="text-xs text-red-600 dark:text-red-400">{problem}</p>
      ) : preview && (
        <div className="text-xs text-zinc-600 dark:text-zinc-400" aria-label="Schedule preview">
          <p className="font-medium">{preview.summary}</p>
          {preview.next_dates.length > 0 && (
            <ul className="mt-1 list-inside list-disc">
              {preview.next_dates.map((date) => <li key={date}>{formatWhen(date, value.timezone.trim() || "UTC")}</li>)}
            </ul>
          )}
        </div>
      )}
    </fieldset>
  );
}

function RunHistory({ routine, onClose, onOpen }: { routine: Routine; onClose: () => void; onOpen: () => void }) {
  const [runs, setRuns] = useState<RoutineRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => api.listRoutineRuns(routine.id).then((r) => live && setRuns(r)).catch((err) => live && setError(errorText(err, "Could not load runs.")));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => { live = false; clearInterval(timer); };
  }, [routine.id]);
  return (
    <div className="space-y-3">
      <button onClick={onClose} className={button}>← Back</button>
      <h3 className="text-sm font-semibold">Runs of {routine.name}</h3>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      {runs === null ? <p className="text-sm text-zinc-500">Loading…</p> : runs.length === 0 ? <p className="text-sm text-zinc-500">No runs yet.</p> : (
        <ul className="space-y-2">
          {runs.map((run) => (
            <li key={run.id} className="rounded-md border border-zinc-200 p-2 text-sm dark:border-zinc-800">
              <div className="flex flex-wrap items-center gap-2">
                <State state={run.state} />
                <span className="text-xs text-zinc-500">{triggerLabel(run.trigger)} · {formatWhen(run.scheduled_for ?? run.created_at, routine.timezone)}</span>
                {run.thread_id !== null && (
                  <Link href={`/chat?thread=${run.thread_id}`} onClick={onOpen} className="ml-auto text-xs underline">Open conversation</Link>
                )}
              </div>
              {(run.error || run.result) && (
                <p className={`mt-1 line-clamp-3 whitespace-pre-wrap text-xs ${run.error ? "text-red-700 dark:text-red-400" : "text-zinc-600 dark:text-zinc-400"}`}>{run.error || run.result}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RoutinesPanel({ agents, onClose }: { agents: Agent[]; onClose: () => void }) {
  const [routines, setRoutines] = useState<Routine[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [history, setHistory] = useState<Routine | null>(null);
  const [secret, setSecret] = useState<{ routineId: number; secret: string; url: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const live = activeAgents(agents);

  const load = useCallback(() => {
    api.listRoutines().then(setRoutines).catch((err) => setError(errorText(err, "Could not load routines.")));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const act = async (work: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      load();
      return true;
    } catch (err) {
      setError(errorText(err, fallback));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    const body = inputOf(draft);
    const ok = await act(() => (draft.id === null ? api.createRoutine(body) : api.updateRoutine(draft.id, body)), "Could not save the routine.");
    if (ok) setDraft(null);
  };

  const agentName = (id: number) => agents.find((agent) => agent.id === id)?.name ?? `Agent ${id}`;
  const heading = draft ? (draft.id === null ? "New routine" : "Edit routine") : "Routines";

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Routines"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="flex max-h-full w-full max-w-2xl flex-col rounded-lg border border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
        <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{heading}</h2>
          <button onClick={onClose} aria-label="Close" className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900">✕</button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">{error}</p>}

          {history ? (
            <RunHistory routine={history} onClose={() => setHistory(null)} onOpen={onClose} />
          ) : draft ? (
            <form className="space-y-3" onSubmit={(event) => { event.preventDefault(); void save(); }}>
              <label className="block text-xs text-zinc-500">Name
                <input aria-label="Routine name" required maxLength={80} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} className={input} />
              </label>
              <label className="block text-xs text-zinc-500">Agent
                <select aria-label="Agent" value={draft.agentId ?? ""} onChange={(e) => setDraft({ ...draft, agentId: Number(e.target.value) })} className={input}>
                  {live.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
                </select>
              </label>
              <label className="block text-xs text-zinc-500">What it should do
                <textarea aria-label="Prompt" required rows={4} value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} className={input} />
              </label>
              <ScheduleFields value={draft.schedule} onChange={(schedule) => setDraft({ ...draft, schedule })} />
              <details className="rounded-md border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                <summary className="cursor-pointer font-medium text-zinc-500">Limits and approvals</summary>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <label>Run limit (minutes)<input type="number" min={1} max={240} value={draft.runLimit} onChange={(e) => setDraft({ ...draft, runLimit: e.target.value })} className={input} /></label>
                  <label>Approval wait (minutes)<input type="number" min={1} max={1440} value={draft.approvalWait} onChange={(e) => setDraft({ ...draft, approvalWait: e.target.value })} className={input} /></label>
                  <label>May start late by (minutes)<input type="number" min={0} max={1440} value={draft.catchUp} onChange={(e) => setDraft({ ...draft, catchUp: e.target.value })} className={input} /></label>
                  <label>Approval mode
                    <select value={draft.approvalMode} onChange={(e) => setDraft({ ...draft, approvalMode: e.target.value as ApprovalMode })} className={input}>
                      {APPROVAL_MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}
                    </select>
                  </label>
                </div>
                <p className="mt-2 text-zinc-500">
                  A run uses the stricter of this mode and the agent&apos;s own. When it needs your approval it waits, tells you,
                  and is denied if nobody answers within the approval wait. Time spent waiting does not count toward the run limit.
                </p>
              </details>
              <div className="flex gap-2">
                <button type="submit" disabled={busy || draft.agentId === null} className="cursor-pointer rounded-md bg-zinc-900 px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900">{busy ? "Saving…" : "Save"}</button>
                <button type="button" onClick={() => setDraft(null)} className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm dark:border-zinc-700">Cancel</button>
              </div>
            </form>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <p className="min-w-0 flex-1 text-xs text-zinc-500">
                  A routine runs a prompt as a new conversation of one of your agents, on a schedule, by webhook, or when you press Run now.
                  The backend must be running for it to run.
                </p>
                <button disabled={live.length === 0} onClick={() => setDraft(newDraft(live))} className={button}>New routine</button>
              </div>
              {live.length === 0 && <p className="text-sm text-zinc-500">Create an agent first: every routine runs as one.</p>}
              {routines === null ? <p className="text-sm text-zinc-500">Loading…</p> : routines.length === 0 ? (
                <p className="text-sm text-zinc-500">No routines yet.</p>
              ) : (
                <ul className="space-y-2">
                  {routines.map((routine) => {
                    const active = isActiveRun(routine.latest_run);
                    return (
                      <li key={routine.id} className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800" aria-label={routine.name}>
                        <div className="flex flex-wrap items-baseline gap-2">
                          <p className="truncate text-sm font-medium">{routine.name}</p>
                          <span className="text-xs text-zinc-500">{agentName(routine.agent_id)}</span>
                          {routine.paused && <span className="rounded bg-zinc-200 px-1.5 text-xs dark:bg-zinc-800">paused</span>}
                          {routine.webhook_enabled && <span className="rounded bg-zinc-200 px-1.5 text-xs dark:bg-zinc-800">webhook</span>}
                        </div>
                        <p className="text-xs text-zinc-500">{routine.schedule_kind === "manual" ? routine.summary : `${routine.summary} · ${nextRunText(routine)}`}</p>
                        {routine.paused_reason && <p className="text-xs text-amber-700 dark:text-amber-400">{routine.paused_reason}</p>}
                        {routine.latest_run && (
                          <p className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                            <span className="text-zinc-500">Latest:</span>
                            <State state={routine.latest_run.state} />
                            <span className="text-zinc-500">{formatWhen(routine.latest_run.scheduled_for ?? routine.latest_run.created_at, routine.timezone)}</span>
                            {routine.latest_run.thread_id !== null && <Link href={`/chat?thread=${routine.latest_run.thread_id}`} onClick={onClose} className="underline">Open conversation</Link>}
                          </p>
                        )}
                        {secret?.routineId === routine.id && (
                          <div className="mt-2 space-y-1 rounded-md border border-amber-300 bg-amber-50 p-2 text-xs dark:border-amber-800 dark:bg-amber-950/30">
                            <p className="font-medium">Copy this secret now: it is shown once.</p>
                            <p>URL: <code className="break-all">{secret.url}</code></p>
                            <p>Secret: <code className="break-all">{secret.secret}</code></p>
                            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-white/60 p-1 dark:bg-black/30">{curlExample(secret.url, secret.secret)}</pre>
                            <button className={button} onClick={() => setSecret(null)}>I have copied it</button>
                          </div>
                        )}
                        <div className="mt-2 flex flex-wrap gap-1">
                          <button disabled={busy || active} title={active ? "A run is in progress" : undefined} onClick={() => void act(() => api.runRoutineNow(routine.id), "Could not start the routine.")} className={button}>Run now</button>
                          <button disabled={busy} onClick={() => void act(() => (routine.paused ? api.resumeRoutine(routine.id) : api.pauseRoutine(routine.id)), "Could not change the routine.")} className={button}>{routine.paused ? "Resume" : "Pause"}</button>
                          <button disabled={busy} onClick={() => setDraft(draftOf(routine))} className={button}>Edit</button>
                          <button disabled={busy} onClick={() => setHistory(routine)} className={button}>History</button>
                          <button disabled={busy} onClick={() => void act(async () => {
                            const result = await api.enableRoutineWebhook(routine.id);
                            setSecret({ routineId: routine.id, secret: result.secret, url: webhookUrl(result.path) });
                          }, "Could not set up the webhook.")} className={button}>{routine.webhook_enabled ? "New webhook secret" : "Add webhook"}</button>
                          {routine.webhook_enabled && <button disabled={busy} onClick={() => void act(() => api.disableRoutineWebhook(routine.id), "Could not turn off the webhook.")} className={button}>Remove webhook</button>}
                          {confirmDelete === routine.id ? (
                            <>
                              <button disabled={busy} onClick={() => void act(() => api.deleteRoutine(routine.id), "Could not delete the routine.").then(() => setConfirmDelete(null))} className={`${button} text-red-700 dark:text-red-400`}>Delete it and its history</button>
                              <button disabled={busy} onClick={() => setConfirmDelete(null)} className={button}>Keep</button>
                            </>
                          ) : (
                            <button disabled={busy} onClick={() => setConfirmDelete(routine.id)} className={`${button} text-red-700 dark:text-red-400`}>Delete</button>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
