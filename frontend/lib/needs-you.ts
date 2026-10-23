export type NeedsYouRow = {
  kind: "signed_out" | "out_of_credit" | "usage_limit" | "approval" | "routine" | "repeated_failure";
  key: string;
  title: string;
  detail: string | null;
  thread_id: number | null;
};

// What the one action on a row says. Every kind has exactly one next step.
const ACTIONS: Record<NeedsYouRow["kind"], string> = {
  signed_out: "Open settings",
  out_of_credit: "Open settings",
  usage_limit: "See usage",
  approval: "Review",
  routine: "Open the run",
  repeated_failure: "Look into it",
};

export function rowAction(row: NeedsYouRow): { label: string; href: string | null } {
  const label = ACTIONS[row.kind] ?? "Open";
  if (row.thread_id !== null) return { label, href: `/chat?thread=${row.thread_id}` };
  if (row.kind === "signed_out" || row.kind === "out_of_credit") return { label, href: "/settings/" };
  return { label, href: null };
}

export function summary(rows: number, more: number): string {
  const total = rows + more;
  return total === 0 ? "Nothing needs you right now." : `${total} thing${total === 1 ? "" : "s"} need${total === 1 ? "s" : ""} you`;
}
