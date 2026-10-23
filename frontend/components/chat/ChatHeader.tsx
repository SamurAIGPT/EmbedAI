"use client";

import { BarChart3, Folder, Menu as MenuIcon, MoreHorizontal, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import type { Agent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";
import { Avatar } from "@/components/ui/Avatar";
import { IconButton } from "@/components/ui/IconButton";
import { Menu, MenuItem } from "@/components/ui/Menu";

const CHIP =
  "inline-flex h-8 max-w-20 shrink-0 cursor-pointer items-center sm:max-w-52 rounded-full border border-line bg-raised px-3 text-[13px] text-ink transition-colors duration-(--dur-fast) hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50 touch:h-10";

// The strip above a conversation: what it is (agent and title), how it runs (engine and model,
// approval mode, computer), and a few quiet icon buttons. Everything else lives in the sidebar
// or opens in a panel. On a phone the secondary buttons fold into one menu.
export function ChatHeader({
  title,
  agent,
  onOpenSidebar,
  agentChoice,
  engine,
  computer,
  approvalMode,
  onApprovals,
  onFiles,
  onUsage,
  usageHint,
  trailing,
  status,
}: {
  title: string;
  // The conversation's agent, for the avatar; null for a plain chat.
  agent: Pick<Agent, "id" | "name"> | null;
  // Present when the sidebar is a drawer: the button that opens it.
  onOpenSidebar?: () => void;
  // Only on a new chat: pick the agent it starts with.
  agentChoice?: { agents: Agent[]; value: number | null; onChange: (id: number | null) => void };
  engine?: { label: string; onClick: () => void; disabled: boolean };
  computer?: { text: string; hint: string; onClick: () => void };
  approvalMode?: { label: string; hint: string };
  onApprovals?: () => void;
  onFiles?: () => void;
  onUsage?: () => void;
  usageHint?: string;
  // The notification bell and anything else that sits at the far right.
  trailing?: ReactNode;
  // Short text such as "Reconnecting…" or the engine's version.
  status?: { text: string; warn?: boolean } | null;
}) {
  type Action = { key: string; label: string; icon: ReactNode; run: () => void; hint?: string };
  const secondary: Action[] = [];
  if (onApprovals) secondary.push({ key: "approvals", label: t("header.approvals"), icon: <ShieldCheck size={18} />, run: onApprovals });
  if (onFiles) secondary.push({ key: "files", label: t("header.files"), icon: <Folder size={18} />, run: onFiles });
  if (onUsage) secondary.push({ key: "usage", label: t("header.usage"), icon: <BarChart3 size={18} />, run: onUsage, hint: usageHint });

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-app px-3 pt-[env(safe-area-inset-top)] sm:px-4">
      {onOpenSidebar && (
        <IconButton label={t("shell.openSidebar")} onClick={onOpenSidebar} className="-ml-1">
          <MenuIcon size={20} />
        </IconButton>
      )}

      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        {agentChoice ? (
          <>
            <Avatar agentId={agentChoice.value} name={agentChoice.agents.find((a) => a.id === agentChoice.value)?.name} size="md" />
            <select
              value={agentChoice.value ?? ""}
              onChange={(e) => agentChoice.onChange(e.target.value === "" ? null : Number(e.target.value))}
              aria-label={t("header.agentForChat")}
              className="h-8 min-w-0 max-w-48 cursor-pointer truncate rounded-field border border-line bg-raised px-2 text-sm font-medium touch:h-10"
            >
              <option value="">{t("header.noAgent")}</option>
              {agentChoice.agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </select>
          </>
        ) : (
          <>
            <Avatar agentId={agent?.id ?? null} name={agent?.name} size="md" />
            <h1 className="min-w-0 truncate text-[15px] font-semibold">
              {title}
              {agent ? <span className="ml-2 hidden text-sm font-normal text-muted sm:inline">{agent.name}</span> : null}
            </h1>
          </>
        )}
      </div>

      {status ? <span className={cn("shrink-0 text-xs", status.warn ? "text-warning" : "hidden text-muted xl:inline")}>{status.text}</span> : null}

      {computer && (
        <button type="button" onClick={computer.onClick} title={computer.hint} className={cn(CHIP, "hidden border-accent/40 text-accent lg:inline-flex")}>
          {computer.text}
        </button>
      )}
      {approvalMode && (
        <span title={approvalMode.hint} className="hidden h-8 shrink-0 items-center rounded-full bg-inset px-3 text-[13px] text-muted xl:inline-flex">
          {approvalMode.label}
        </span>
      )}
      {engine && (
        <button type="button" aria-label={t("header.engine")} disabled={engine.disabled} onClick={engine.onClick} title={engine.label} className={CHIP}>
          <span className="truncate">{engine.label}</span>
        </button>
      )}

      <div className="hidden items-center lg:flex">
        {secondary.map((item) => (
          <IconButton key={item.key} label={item.label} title={item.hint} onClick={item.run}>
            {item.icon}
          </IconButton>
        ))}
      </div>
      {secondary.length > 0 || computer ? (
        <div className="lg:hidden">
          <Menu
            trigger={
              <IconButton label={t("header.more")}>
                <MoreHorizontal size={18} />
              </IconButton>
            }
          >
            {computer && <MenuItem onSelect={computer.onClick}>{computer.text}</MenuItem>}
            {secondary.map((item) => (
              <MenuItem key={item.key} onSelect={item.run} icon={item.icon}>{item.label}</MenuItem>
            ))}
          </Menu>
        </div>
      ) : null}
      {trailing}
    </header>
  );
}
