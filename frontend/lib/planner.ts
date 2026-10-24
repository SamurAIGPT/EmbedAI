import type { WorkItem } from "./planner-api";

export function dateKey(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function addDays(date: Date, days: number): Date {
  const result = new Date(date);
  result.setDate(result.getDate() + days);
  return result;
}

export function calendarDays(anchor: Date, view: "month" | "week"): Date[] {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), view === "month" ? 1 : anchor.getDate());
  first.setDate(first.getDate() - (first.getDay() + 6) % 7);
  return Array.from({ length: view === "month" ? 42 : 7 }, (_, i) => addDays(first, i));
}

export function deadlineDay(item: Pick<WorkItem, "due_date" | "due_at">): string | null {
  return item.due_date ?? (item.due_at ? dateKey(new Date(item.due_at)) : null);
}

export function isOverdue(item: Pick<WorkItem, "due_date" | "due_at" | "completed_at" | "archived">, now = new Date()): boolean {
  if (item.archived || item.completed_at) return false;
  if (item.due_at) return new Date(item.due_at).getTime() < now.getTime();
  return !!item.due_date && item.due_date < dateKey(now);
}

export function localDateTime(iso: string): string {
  const date = new Date(iso);
  return `${dateKey(date)}T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

// Date-only values remain dates; timed deadlines retain local wall time across DST.
export function rescheduleDeadline(item: Pick<WorkItem, "due_date" | "due_at">, day: string, timezone: string) {
  if (!item.due_at) return { due_date: day, due_at: null };
  const time = localDateTime(item.due_at).slice(11);
  const date = new Date(`${day}T${time}`);
  if (localDateTime(date.toISOString()) !== `${day}T${time}`) throw new Error("That local time does not exist on this date. Edit the deadline to choose another time.");
  return { due_date: null, due_at: date.toISOString(), timezone };
}
