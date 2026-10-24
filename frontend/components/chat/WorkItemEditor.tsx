"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import type { Agent, Thread } from "@/lib/api";
import { localDateTime } from "@/lib/planner";
import type { WorkBoard, WorkInput, WorkItem } from "@/lib/planner-api";
import { browserTimezone } from "@/lib/routines";

const input = "w-full rounded-field border border-line bg-surface px-3 py-2 text-sm text-ink disabled:opacity-50";
type DeadlineKind = "none" | "date" | "time";

function draftOf(item: WorkItem | null, board: WorkBoard, seedDate?: string, seedColumn?: number) {
  return {
    title: item?.title ?? "",
    description: item?.description ?? "",
    columnId: item?.column_id ?? seedColumn ?? board.columns[0]?.id ?? 0,
    priority: item?.priority ?? "normal" as WorkItem["priority"],
    deadlineKind: (item?.due_at ? "time" : item?.due_date || seedDate ? "date" : "none") as DeadlineKind,
    date: item?.due_date ?? seedDate ?? "",
    time: item?.due_at ? localDateTime(item.due_at) : seedDate ? `${seedDate}T09:00` : "",
    assignee: item?.agent_id ? `agent:${item.agent_id}` : item?.team_id ? `team:${item.team_id}` : "",
    threadId: item?.thread_id ?? null,
  };
}

export type WorkItemEditorProps = {
  item: WorkItem | null;
  board: WorkBoard;
  agents: Agent[];
  threads: Thread[];
  team: { id: number; name: string } | null;
  seedDate?: string;
  seedColumn?: number;
  latestItem?: WorkItem;
  onReload?: (item: WorkItem) => void;
  onOpenThread?: () => void;
  onSave: (data: WorkInput) => Promise<void>;
  onClose: () => void;
};

export function WorkItemEditor({ item, board, agents, threads, team, seedDate, seedColumn, latestItem, onReload, onOpenThread, onSave, onClose }: WorkItemEditorProps) {
  const id = useId();
  const [draft, setDraft] = useState(() => draftOf(item, board, seedDate, seedColumn));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timezone = browserTimezone();
  const stale = !!item && !!latestItem && latestItem.revision !== item.revision;
  const unavailableAgent = item?.agent_id && !agents.some(agent => agent.id === item.agent_id);
  const unavailableTeam = item?.team_id && team?.id !== item.team_id;
  const unavailableThread = item?.thread_id && !threads.some(thread => thread.id === item.thread_id);
  const change = <K extends keyof typeof draft>(key: K, value: typeof draft[K]) => setDraft(current => ({ ...current, [key]: value }));

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || stale) return;
    setError(null);
    if (!draft.title.trim()) { setError("Give the task a title."); return; }
    let dueAt: string | null = null;
    let dueDate: string | null = null;
    let savedZone = timezone;
    if (draft.deadlineKind === "date") {
      if (!draft.date) { setError("Choose a deadline date."); return; }
      dueDate = draft.date;
    } else if (draft.deadlineKind === "time") {
      if (!draft.time) { setError("Choose a deadline date and time."); return; }
      if (item?.due_at && localDateTime(item.due_at) === draft.time) {
        dueAt = item.due_at;
        savedZone = item.timezone;
      } else {
        const date = new Date(draft.time);
        if (!Number.isFinite(date.getTime()) || localDateTime(date.toISOString()) !== draft.time) {
          setError("That local time does not exist on this date. Choose another time.");
          return;
        }
        dueAt = date.toISOString();
      }
    }
    const [kind, assigneeId] = draft.assignee.split(":");
    const data: WorkInput = {
      board_id: board.id, column_id: draft.columnId, title: draft.title.trim(), description: draft.description,
      priority: draft.priority, due_at: dueAt, due_date: dueDate, timezone: savedZone,
      agent_id: kind === "agent" ? Number(assigneeId) : null,
      team_id: kind === "team" ? Number(assigneeId) : null,
      thread_id: draft.threadId,
    };
    setBusy(true);
    try { await onSave(data); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not save this task. Try again."); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open onOpenChange={open => { if (!open && !busy) onClose(); }} title={item ? "Edit task" : "New task"} size="wide"
      description="Plan work, set a deadline, and link a conversation.">
      <form onSubmit={save} className="space-y-4">
        {stale && <div role="alert" className="space-y-2 rounded-field border border-warning/50 bg-warning/10 p-3 text-sm">
          <p>This task changed in another window. Load the latest version before saving. This replaces your unsaved edits.</p>
          {onReload && <Button size="sm" onClick={() => { setDraft(draftOf(latestItem!, board)); setError(null); onReload(latestItem!); }}>Load latest version</Button>}
        </div>}
        <fieldset disabled={busy} className="space-y-4">
          <div>
            <label htmlFor={`${id}-title`} className="mb-1 block text-sm font-medium">Title</label>
            <input id={`${id}-title`} value={draft.title} onChange={event => change("title", event.target.value)} required maxLength={240} className={input} placeholder="What needs to happen?" />
          </div>
          <div>
            <label htmlFor={`${id}-description`} className="mb-1 block text-sm font-medium">Description</label>
            <textarea id={`${id}-description`} value={draft.description} onChange={event => change("description", event.target.value)} maxLength={50000} rows={4} className={input} placeholder="Add context, a checklist, or the expected result…" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor={`${id}-status`} className="mb-1 block text-sm font-medium">Status</label>
              <select id={`${id}-status`} value={draft.columnId} onChange={event => change("columnId", Number(event.target.value))} className={input}>
                {board.columns.map(column => <option key={column.id} value={column.id}>{column.name}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor={`${id}-priority`} className="mb-1 block text-sm font-medium">Priority</label>
              <select id={`${id}-priority`} value={draft.priority} onChange={event => change("priority", event.target.value as WorkItem["priority"])} className={input}>
                <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option>
              </select>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor={`${id}-deadline-kind`} className="mb-1 block text-sm font-medium">Deadline</label>
              <select id={`${id}-deadline-kind`} value={draft.deadlineKind} onChange={event => change("deadlineKind", event.target.value as DeadlineKind)} className={input}>
                <option value="none">No deadline</option><option value="date">Date only</option><option value="time">Date and time</option>
              </select>
            </div>
            {draft.deadlineKind === "date" && <div>
              <label htmlFor={`${id}-date`} className="mb-1 block text-sm font-medium">Due date</label>
              <input id={`${id}-date`} type="date" required value={draft.date} onChange={event => change("date", event.target.value)} className={input} />
            </div>}
            {draft.deadlineKind === "time" && <div>
              <label htmlFor={`${id}-time`} className="mb-1 block text-sm font-medium">Due at · {timezone}</label>
              <input id={`${id}-time`} type="datetime-local" required value={draft.time} onChange={event => change("time", event.target.value)} className={input} />
            </div>}
          </div>
          <p className="text-xs text-muted">Deadlines and status changes do not start an agent. Use Routines to schedule agent work.</p>
          <div>
            <label htmlFor={`${id}-assignee`} className="mb-1 block text-sm font-medium">Assigned to</label>
            <select id={`${id}-assignee`} value={draft.assignee} onChange={event => change("assignee", event.target.value)} className={input}>
              <option value="">Unassigned</option>
              {team && <option value={`team:${team.id}`}>Team · {team.name}</option>}
              {agents.map(agent => <option key={agent.id} value={`agent:${agent.id}`}>{agent.name}</option>)}
              {unavailableAgent && <option value={`agent:${item!.agent_id}`}>Unavailable agent · #{item!.agent_id}</option>}
              {unavailableTeam && <option value={`team:${item!.team_id}`}>Unavailable team · #{item!.team_id}</option>}
            </select>
          </div>
          <div>
            <label htmlFor={`${id}-thread`} className="mb-1 block text-sm font-medium">Linked conversation</label>
            <select id={`${id}-thread`} value={draft.threadId ?? ""} onChange={event => change("threadId", event.target.value ? Number(event.target.value) : null)} className={input}>
              <option value="">No linked conversation</option>
              {threads.map(thread => <option key={thread.id} value={thread.id}>{thread.title || `Conversation #${thread.id}`}</option>)}
              {unavailableThread && <option value={item!.thread_id!}>Unavailable conversation · #{item!.thread_id}</option>}
            </select>
            {draft.threadId !== null && threads.some(thread => thread.id === draft.threadId) && <Link href={`/chat?thread=${draft.threadId}`} className="mt-2 inline-block text-xs text-accent underline" onClick={event => {
              if (busy) { event.preventDefault(); return; }
              (onOpenThread ?? onClose)();
            }}>Open linked conversation</Link>}
          </div>
        </fieldset>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        <div className="flex justify-end gap-2 border-t border-line pt-4">
          <Button disabled={busy} onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" loading={busy} disabled={stale || !draft.title.trim()}>{item ? "Save changes" : "Create task"}</Button>
        </div>
      </form>
    </Dialog>
  );
}
