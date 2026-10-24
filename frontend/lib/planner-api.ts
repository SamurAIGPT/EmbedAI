import { request } from "./api";

export type WorkColumn = { id: number; key: string; name: string; position: number };
export type WorkBoard = { id: number; name: string; columns: WorkColumn[] };
export type WorkItem = {
  id: number; board_id: number; column_id: number; title: string; description: string;
  priority: "low" | "normal" | "high" | "urgent"; due_date: string | null; due_at: string | null;
  timezone: string; agent_id: number | null; team_id: number | null; thread_id: number | null;
  position: number; revision: number; archived: boolean; completed_at: string | null;
  created_at: string; updated_at: string;
};
export type WorkInput = Pick<WorkItem, "board_id" | "column_id" | "title" | "description" | "priority" | "due_date" | "due_at" | "timezone" | "agent_id" | "team_id" | "thread_id">;
export type WorkChanges = Partial<Omit<WorkInput, "board_id">> & { archived?: boolean };
export type CalendarEvent = { id: string; kind: "routine" | "run"; routine_id: number; title: string; at: string; state: string; thread_id: number | null };
export type RoutineCalendar = { events: CalendarEvent[]; warnings: string[] };

export const planner = {
  board: () => request<WorkBoard>("/planner/board"),
  items: (board: number, archived = false) => request<WorkItem[]>(`/planner/items?board_id=${board}&archived=${archived}`),
  item: (id: number) => request<WorkItem>(`/planner/items/${id}`),
  create: (data: WorkInput) => request<WorkItem>("/planner/items", { method: "POST", body: JSON.stringify(data) }),
  update: (item: Pick<WorkItem, "id" | "revision">, data: WorkChanges) => request<WorkItem>(`/planner/items/${item.id}`, { method: "PATCH", body: JSON.stringify({ ...data, revision: item.revision }) }),
  calendar: (start: string, end: string) => request<RoutineCalendar>(`/planner/routine-calendar?${new URLSearchParams({ start, end })}`),
};
