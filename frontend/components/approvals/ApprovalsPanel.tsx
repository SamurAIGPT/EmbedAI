"use client";

import { useState } from "react";
import { Dialog } from "@/components/ui/Dialog";
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
      <div role="tablist" className="mb-4 flex gap-1 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`-mb-px cursor-pointer border-b-2 px-3 py-1.5 text-xs font-medium ${
              tab === t.id
                ? "border-accent"
                : "border-transparent text-muted hover:text-ink"
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
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title="Approvals" size="wide">
      <ApprovalsTabs threadId={threadId} workspace={workspace} />
    </Dialog>
  );
}
