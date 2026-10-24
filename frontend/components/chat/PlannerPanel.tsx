"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { CalendarDays, ChevronLeft, ChevronRight, Columns3, List, Plus, RefreshCw } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { WorkItemEditor } from "./WorkItemEditor";
import { ApiError, type Agent, type Thread } from "@/lib/api";
import { collaboration } from "@/lib/collaboration";
import { browserTimezone } from "@/lib/routines";
import { addDays, calendarDays, dateKey, deadlineDay, isOverdue, rescheduleDeadline } from "@/lib/planner";
import { planner, type CalendarEvent, type RoutineCalendar, type WorkBoard, type WorkChanges, type WorkInput, type WorkItem } from "@/lib/planner-api";
import { cn } from "@/lib/cn";

type View = "board" | "month" | "week" | "list";
type Editor = { item: WorkItem | null; seedDate?: string; seedColumn?: number };
const control = "rounded-field border border-line bg-surface px-2 py-1.5 text-sm disabled:opacity-50";
const dragType = "application/x-open-dots-work-item";
const priorities = ["low", "normal", "high", "urgent"] as const;
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "Could not load the planner.";

export function PlannerPanel({ agents, threads, onClose, onOpenRoutines }: {
  agents: Agent[]; threads: Thread[]; onClose: () => void; onOpenRoutines: () => void;
}) {
  const [board, setBoard] = useState<WorkBoard | null>(null);
  const [items, setItems] = useState<WorkItem[] | null>(null);
  const [team, setTeam] = useState<{ id: number; name: string } | null>(null);
  const [view, setView] = useState<View>("board");
  const [anchor, setAnchor] = useState(() => new Date());
  const [query, setQuery] = useState("");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState("");
  const [onlyOverdue, setOnlyOverdue] = useState(false);
  const [archived, setArchived] = useState(false);
  const [showRoutines, setShowRoutines] = useState(true);
  const [calendar, setCalendar] = useState<RoutineCalendar | null>(null);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [conflictItem, setConflictItem] = useState<WorkItem | null>(null);
  const mutationVersion = useRef(0);
  const saving = useRef(false);
  const timezone = browserTimezone();

  useEffect(() => {
    let cancelled = false;
    planner.board().then(value => { if (!cancelled) setBoard(value); }).catch(error => { if (!cancelled) setError(errorMessage(error)); });
    collaboration.team().then(value => { if (!cancelled) setTeam(value.team); }).catch(() => { /* Assignment to agents remains available. */ });
    return () => { cancelled = true; };
  }, [tick]);

  const refreshItems = useCallback(async () => {
    if (!board) return;
    const version = mutationVersion.current;
    const values = await planner.items(board.id, archived);
    if (version === mutationVersion.current) setItems(values);
  }, [board, archived]);

  useEffect(() => {
    if (!board) return;
    let cancelled = false;
    const load = async () => {
      const version = mutationVersion.current;
      try {
        const values = await planner.items(board.id, archived);
        if (!cancelled && !saving.current && version === mutationVersion.current) { setItems(values); setError(null); }
      } catch (error) { if (!cancelled) setError(errorMessage(error)); }
    };
    void load();
    const timer = setInterval(() => void load(), 30_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [board, archived]);

  const calendarView = view === "week" ? "week" : "month";
  const days = useMemo(() => calendarDays(anchor, calendarView), [anchor, calendarView]);
  const start = days[0].toISOString();
  const end = addDays(days[days.length - 1], 1).toISOString();
  useEffect(() => {
    if (!showRoutines || (view !== "month" && view !== "week")) return;
    let cancelled = false;
    const load = async () => {
      try {
        const value = await planner.calendar(start, end);
        if (!cancelled) { setCalendar(value); setCalendarError(null); }
      } catch (error) { if (!cancelled) { setCalendar(null); setCalendarError(errorMessage(error)); } }
    };
    void load();
    const timer = setInterval(() => void load(), 60_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [start, end, view, showRoutines, tick]);

  const filtered = useMemo(() => (items ?? []).filter(item => {
    const matches = `${item.title} ${item.description}`.toLowerCase().includes(query.trim().toLowerCase());
    const matchesAssignee = !assignee || (assignee === "unassigned" ? !item.agent_id && !item.team_id
      : assignee.startsWith("team:") ? String(item.team_id) === assignee.slice(5) : String(item.agent_id) === assignee);
    return matches && matchesAssignee && (!priority || item.priority === priority) && (!onlyOverdue || isOverdue(item));
  }), [items, query, assignee, priority, onlyOverdue]);
  const upcoming = [...filtered].sort((a, b) => {
    const instant = (item: WorkItem) => item.due_at ? new Date(item.due_at).getTime()
      : item.due_date ? new Date(`${item.due_date}T23:59:59.999`).getTime() : Infinity;
    return instant(a) - instant(b) || a.position - b.position;
  });

  const update = async (item: WorkItem, changes: WorkChanges) => {
    if (saving.current) return;
    saving.current = true; mutationVersion.current += 1;
    setBusy(true); setError(null);
    try {
      const saved = await planner.update(item, changes);
      setItems(current => (current ?? []).flatMap(value => value.id !== saved.id ? [value] : saved.archived === archived ? [saved] : []));
    } catch (error) {
      setError(errorMessage(error));
      await refreshItems().catch(() => {});
    } finally { saving.current = false; mutationVersion.current += 1; setBusy(false); }
  };

  const save = async (data: WorkInput) => {
    if (saving.current) throw new Error("Another task is saving. Try again in a moment.");
    saving.current = true; mutationVersion.current += 1;
    setBusy(true);
    try {
      const changes = Object.fromEntries(Object.entries(data).filter(([key, value]) => key !== "board_id" && value !== editor?.item?.[key as keyof WorkItem])) as WorkChanges;
      const saved = editor?.item ? await planner.update(editor.item, changes) : await planner.create(data);
      if (!editor?.item && archived) { setItems(null); setArchived(false); }
      else setItems(current => [...(current ?? []).filter(value => value.id !== saved.id), ...(saved.archived === archived ? [saved] : [])]);
      setEditor(null);
      setConflictItem(null);
      setError(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && editor?.item) {
        await planner.item(editor.item.id).then(setConflictItem).catch(() => {});
      }
      await refreshItems().catch(() => {});
      throw error;
    } finally { saving.current = false; mutationVersion.current += 1; setBusy(false); }
  };

  const drop = (event: React.DragEvent, changes: (item: WorkItem) => WorkChanges) => {
    event.preventDefault();
    if (busy || archived) return;
    const id = Number(event.dataTransfer.getData(dragType));
    const item = items?.find(item => item.id === id);
    if (!item) return;
    try { void update(item, changes(item)); }
    catch (error) { setError(errorMessage(error)); }
  };
  const acceptDrop = (event: React.DragEvent) => { if (!busy && !archived && event.dataTransfer.types.includes(dragType)) event.preventDefault(); };
  const assigneeLabel = (item: WorkItem) => item.agent_id ? agents.find(agent => agent.id === item.agent_id)?.name ?? "Agent unavailable"
    : item.team_id ? (team?.id === item.team_id ? team.name : "Team unavailable") : "Unassigned";
  const deadlineLabel = (item: WorkItem) => item.due_at ? new Date(item.due_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : item.due_date ? new Date(`${item.due_date}T12:00`).toLocaleDateString([], { month: "short", day: "numeric" }) : "No due date";

  const taskCard = (item: WorkItem, compact = false) => (
    <article key={item.id} draggable={!busy && !archived} onDragStart={event => { event.dataTransfer.setData(dragType, String(item.id)); event.dataTransfer.effectAllowed = "move"; }}
      className={cn("rounded-field border border-line bg-raised p-2.5 text-sm shadow-sm", isOverdue(item) && "border-danger/60", compact && "p-1.5 text-xs")}>
      <button type="button" onClick={() => setEditor({ item })} className="w-full break-words text-left font-medium hover:underline">{item.title}</button>
      <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span className={cn(item.priority === "urgent" ? "text-danger" : item.priority === "high" ? "text-warning" : "")}>{item.priority}</span>
        <span className={isOverdue(item) ? "text-danger" : ""}>{isOverdue(item) ? "Overdue · " : ""}{deadlineLabel(item)}</span>
      </div>
      {!compact && <>
        <p className="mt-2 truncate text-xs text-muted">{assigneeLabel(item)}</p>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <select aria-label={`Move ${item.title}`} className={`${control} min-w-0 max-w-full text-xs`} value={item.column_id} disabled={busy || archived} onChange={event => void update(item, { column_id: Number(event.target.value) })}>
            {board?.columns.map(column => <option key={column.id} value={column.id}>{column.name}</option>)}
          </select>
          <button type="button" className="text-xs text-muted hover:underline disabled:opacity-50" disabled={busy} onClick={() => void update(item, { archived: !item.archived })}>{archived ? "Restore" : "Archive"}</button>
        </div>
      </>}
    </article>
  );

  const routineEntry = (event: CalendarEvent) => (
    <div key={event.id} className="rounded-field border border-accent/30 bg-accent/5 px-2 py-1 text-xs text-accent">
      {event.thread_id ? <Link className="block truncate hover:underline" href={`/chat?thread=${event.thread_id}`} onClick={onClose}>↻ {event.title}</Link>
        : <button className="block w-full truncate text-left hover:underline" onClick={onOpenRoutines}>↻ {event.title}</button>}
      <p className="text-muted">{new Date(event.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {event.state}</p>
    </div>
  );

  const calendarMode = view === "month" || view === "week";
  return <>
    <Dialog open onOpenChange={open => { if (!open) onClose(); }} title="Tasks & calendar" description="Plan your work, track deadlines, and see scheduled routines." size="xl" flush>
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="space-y-3 border-b border-line p-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex rounded-field border border-line p-0.5" aria-label="Planner views">
              {([["board", "Board", Columns3], ["month", "Month", CalendarDays], ["week", "Week", CalendarDays], ["list", "Upcoming", List]] as const).map(([value, label, Icon]) =>
                <button key={value} type="button" aria-pressed={view === value} onClick={() => setView(value)} className={cn("flex items-center gap-1.5 rounded px-2 py-1.5 text-sm", view === value ? "bg-accent text-accent-fg" : "text-muted hover:bg-hover")}><Icon size={14} aria-hidden="true" />{label}</button>)}
            </div>
            <span className="mr-auto text-xs text-muted">{filtered.length} task{filtered.length === 1 ? "" : "s"} · {timezone}</span>
            <Button disabled={busy} onClick={() => { setTick(value => value + 1); }} icon={<RefreshCw size={14} aria-hidden="true" />}>Refresh</Button>
            <Button variant="primary" disabled={!board || busy} onClick={() => setEditor({ item: null })} icon={<Plus size={14} aria-hidden="true" />}>New task</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <input aria-label="Search tasks" type="search" placeholder="Search tasks…" value={query} onChange={event => setQuery(event.target.value)} className={`${control} min-w-32 flex-1`} />
            <select aria-label="Filter by assignment" className={control} value={assignee} onChange={event => setAssignee(event.target.value)}>
              <option value="">All assignments</option><option value="unassigned">Unassigned</option>
              {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
              {team && <option value={`team:${team.id}`}>{team.name} (team)</option>}
            </select>
            <select aria-label="Filter by priority" className={control} value={priority} onChange={event => setPriority(event.target.value)}>
              <option value="">All priorities</option>{priorities.map(value => <option key={value} value={value}>{value}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={onlyOverdue} onChange={event => setOnlyOverdue(event.target.checked)} />Overdue</label>
            <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={archived} disabled={busy} onChange={event => { setItems(null); setArchived(event.target.checked); }} />Archived</label>
          </div>
        </div>
        <div aria-live="polite" className="px-4 text-sm">
          {error && <p role="alert" className="py-2 text-danger">{error}</p>}
          {!items && !error && <p className="py-4 text-muted">Loading tasks…</p>}
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          {items && board && view === "board" && <div className="grid min-h-[45dvh] grid-cols-1 gap-3 sm:grid-cols-[repeat(6,minmax(210px,1fr))]">
            {board.columns.map(column => {
              const cards = filtered.filter(item => item.column_id === column.id).sort((a, b) => a.position - b.position || a.id - b.id);
              return <section key={column.id} aria-label={column.name} onDragOver={acceptDrop} onDrop={event => drop(event, () => ({ column_id: column.id }))} className="min-w-0 rounded-card border border-line bg-inset p-2">
                <div className="mb-3 flex items-center gap-2 px-1"><h3 className="text-sm font-semibold">{column.name}</h3><span className="text-xs text-muted">{cards.length}</span>
                  <button type="button" aria-label={`Add task to ${column.name}`} disabled={busy} onClick={() => setEditor({ item: null, seedColumn: column.id })} className="ml-auto rounded p-1 hover:bg-hover"><Plus size={15} aria-hidden="true" /></button></div>
                <div className="space-y-2">{cards.map(item => taskCard(item))}{cards.length === 0 && <p className="p-3 text-xs text-muted">No tasks here.</p>}</div>
              </section>;
            })}
          </div>}
          {items && view === "list" && <div className="space-y-3">
            <p className="text-xs text-muted">Deadlines first, followed by tasks without a due date. Completed tasks keep their workflow state.</p>
            {upcoming.length === 0 ? <p className="py-10 text-center text-sm text-muted">No matching tasks. Create a task or adjust your filters.</p>
              : <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{upcoming.map(item => taskCard(item))}</div>}
          </div>}
          {calendarMode && <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <button className={control} aria-label="Previous period" onClick={() => setAnchor(value => view === "week" ? addDays(value, -7) : new Date(value.getFullYear(), value.getMonth() - 1, 1))}><ChevronLeft size={16} aria-hidden="true" /></button>
              <button className={control} onClick={() => setAnchor(new Date())}>Today</button>
              <button className={control} aria-label="Next period" onClick={() => setAnchor(value => view === "week" ? addDays(value, 7) : new Date(value.getFullYear(), value.getMonth() + 1, 1))}><ChevronRight size={16} aria-hidden="true" /></button>
              <h3 className="mr-auto text-sm font-semibold">{view === "month" ? anchor.toLocaleDateString([], { month: "long", year: "numeric" }) : `${days[0].toLocaleDateString([], { month: "short", day: "numeric" })} – ${days[6].toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`}</h3>
              <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={showRoutines} onChange={event => setShowRoutines(event.target.checked)} />Routines & runs</label>
              <button className={`${control} text-xs`} onClick={onOpenRoutines}>Manage routines</button>
            </div>
            {showRoutines && calendarError && <p role="alert" className="text-sm text-danger">Routine calendar: {calendarError}</p>}
            {showRoutines && calendar?.warnings.map(warning => <p key={warning} className="text-xs text-warning">{warning}</p>)}
            <p className="text-xs text-muted">Task deadlines · <span className="text-accent">↻ Routines and run history</span>. All times are in {timezone}. Use + on a date to add a task.</p>
            <div className="overflow-x-auto rounded-field border border-line">
              <div className="min-w-[700px]">
                <div className="grid grid-cols-7 border-b border-line bg-inset">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(day => <div key={day} className="p-2 text-center text-xs font-semibold text-muted">{day}</div>)}</div>
                <div className="grid grid-cols-7">
                  {days.map(day => {
                    const key = dateKey(day);
                    const cards = filtered.filter(item => deadlineDay(item) === key);
                    const events = showRoutines ? (calendar?.events ?? []).filter(event => dateKey(new Date(event.at)) === key) : [];
                    return <section key={key} aria-label={day.toLocaleDateString([], { dateStyle: "full" })} onDragOver={acceptDrop} onDrop={event => drop(event, item => rescheduleDeadline(item, key, timezone))}
                      className={cn("min-h-28 border-b border-r border-line p-1.5", view === "week" && "min-h-[40dvh]", view === "month" && day.getMonth() !== anchor.getMonth() && "bg-inset/50")}>
                      <div className="mb-1 flex items-center justify-between gap-1"><span className={cn("inline-flex size-6 items-center justify-center rounded-full text-xs", key === dateKey(new Date()) && "bg-accent font-semibold text-accent-fg")}>{day.getDate()}</span>
                        <button type="button" aria-label={`Add task on ${key}`} disabled={!board || busy} className="rounded p-1 text-muted hover:bg-hover" onClick={() => setEditor({ item: null, seedDate: key })}><Plus size={13} aria-hidden="true" /></button></div>
                      <div className="space-y-1">{cards.slice(0, 3).map(item => taskCard(item, true))}{events.slice(0, 2).map(routineEntry)}
                        {(cards.length > 3 || events.length > 2) && <details><summary className="cursor-pointer py-1 text-xs text-muted">{Math.max(0, cards.length - 3) + Math.max(0, events.length - 2)} more</summary><div className="space-y-1">{cards.slice(3).map(item => taskCard(item, true))}{events.slice(2).map(routineEntry)}</div></details>}
                      </div>
                    </section>;
                  })}
                </div>
              </div>
            </div>
            {filtered.some(item => !deadlineDay(item)) && <details><summary className="cursor-pointer text-sm text-muted">Tasks without a due date ({filtered.filter(item => !deadlineDay(item)).length})</summary><div className="mt-2 grid gap-2 sm:grid-cols-3">{filtered.filter(item => !deadlineDay(item)).map(item => taskCard(item))}</div></details>}
          </div>}
        </div>
      </div>
    </Dialog>
    {editor && board && <WorkItemEditor key={editor.item?.id ?? "new"} item={editor.item} board={board} agents={agents} threads={threads} team={team}
      seedDate={editor.seedDate} seedColumn={editor.seedColumn} latestItem={conflictItem && conflictItem.id === editor.item?.id ? conflictItem : items?.find(item => item.id === editor.item?.id)} onReload={item => { setEditor({ item }); setConflictItem(null); }} onSave={save} onOpenThread={onClose} onClose={() => { if (!busy) { setEditor(null); setConflictItem(null); } }} />}
  </>;
}
