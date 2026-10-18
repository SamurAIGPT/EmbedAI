"use client";

import { useEffect, useState } from "react";
import { ApprovalRules } from "./ApprovalRules";
import { DecisionLog } from "./DecisionLog";
import { HealthScan } from "./HealthScan";

const TABS = [
  { id: "rules", label: "Always allowed" },
  { id: "decisions", label: "Decisions" },
  { id: "health", label: "Health" },
] as const;

type Tab = (typeof TABS)[number]["id"];

export function ApprovalsTabs({
  threadId = null,
  workspace = null,
}: {
  threadId?: number | null;
  workspace?: string | null;
}) {
  const [tab, setTab] = useState<Tab>("rules");
  return (
    <div>
      <div role="tablist" className="mb-4 flex gap-1 border-b border-zinc-200 dark:border-zinc-800">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px cursor-pointer border-b-2 px-3 py-1.5 text-xs font-medium ${
              tab === t.id
                ? "border-zinc-900 dark:border-zinc-100"
                : "border-transparent text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "rules" && <ApprovalRules workspace={workspace} threadId={threadId} />}
        {tab === "decisions" && <DecisionLog threadId={threadId} />}
        {tab === "health" && <HealthScan />}
      </div>
    </div>
  );
}

// The Chat page's dialog for approvals: saved rules, the decision log and the scan.
export function ApprovalsPanel({
  threadId,
  workspace,
  onClose,
}: {
  threadId: number | null;
  workspace: string | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Approvals"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="flex max-h-full w-full max-w-2xl flex-col rounded-lg border border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
        <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <h2 className="flex-1 text-sm font-semibold">Approvals</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900"
          >
            ✕
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          <ApprovalsTabs threadId={threadId} workspace={workspace} />
        </div>
      </div>
    </div>
  );
}
