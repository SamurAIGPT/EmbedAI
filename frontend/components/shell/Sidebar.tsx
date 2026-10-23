"use client";

import { Archive, CalendarClock, Plus, Settings, SlidersHorizontal } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import type { Agent, Thread } from "@/lib/api";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";
import { agentTarget, rowStatus, sidebarAgents } from "@/lib/sidebar";
import { ConversationSearch } from "@/components/chat/ConversationSearch";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { StatusDot } from "./StatusDot";
import { ThreadRow, type RowActions } from "./ThreadRow";

export type SidebarLive = {
  // The open conversation and its agent are the only ones whose live state this page knows.
  threadId: number | null;
  agentId: number | null;
  working: boolean;
  waiting: boolean;
};

function SectionHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-3 pb-1 pt-3">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h2>
      <div className="flex items-center">{children}</div>
    </div>
  );
}

const ROW = "flex w-full cursor-pointer items-center gap-2.5 rounded-field py-2 pl-2 pr-2 text-left text-sm transition-colors duration-(--dur-fast) touch:py-2.5";

// The app's left column: start a chat, search, the agents, every recent conversation, and the
// places that are not conversations. Agents and Recent are separate views of the same
// conversations; nothing is nested under an agent.
export function Sidebar({
  agents,
  threads,
  selectedThreadId,
  selectedAgentId,
  live,
  showArchived,
  onToggleArchived,
  actions,
  error,
  onNewChat,
  onManageAgents,
  onNewAgent,
  onOpenRoutines,
  onNavigate,
  collaboration,
}: {
  // null: the backend has no agents (an older one), so the section is left out.
  agents: Agent[] | null;
  // null while the first load is under way.
  threads: Thread[] | null;
  selectedThreadId: number | null;
  // The agent the open or about-to-start conversation belongs to.
  selectedAgentId: number | null;
  live: SidebarLive;
  showArchived: boolean;
  onToggleArchived: () => void;
  actions: RowActions;
  // What went wrong with the last rename, archive or delete, if anything.
  error: string | null;
  // Start a conversation: with the given agent, or (no argument) with whatever is chosen now.
  onNewChat: (agentId?: number) => void;
  onManageAgents?: () => void;
  // Open the new-agent form. Offered even when there are no agents yet.
  onNewAgent?: () => void;
  onOpenRoutines?: () => void;
  // Lets a drawer close itself after a choice.
  onNavigate?: () => void;
  collaboration?: React.ReactNode;
}) {
  const [searching, setSearching] = useState(false);
  const names = new Map((agents ?? []).map((agent) => [agent.id, agent.name]));
  const listed = sidebarAgents(agents);

  const newChat = (agentId?: number) => {
    onNewChat(agentId);
    onNavigate?.();
  };

  return (
    <nav aria-label={t("shell.conversations")} className="flex h-full min-h-0 flex-col bg-surface text-ink">
      <div className="space-y-2 p-3 pb-1">
        <Button className="w-full" icon={<Plus size={16} aria-hidden="true" />} onClick={() => newChat()}>
          {t("shell.newChat")}
        </Button>
      </div>
      <ConversationSearch agentNames={names} onActive={setSearching} onNavigate={onNavigate} />

      <div className={cn("min-h-0 flex-1 overflow-y-auto px-2 pb-2", searching && "hidden")}>
        {agents !== null && (
          <section aria-label={t("sidebar.agents")}>
            <SectionHeader title={t("sidebar.agents")}>
              {onNewAgent && (
                <IconButton label={t("sidebar.newAgent")} onClick={() => { onNewAgent(); onNavigate?.(); }} className="size-7 touch:size-10">
                  <Plus size={14} />
                </IconButton>
              )}
              {onManageAgents && listed.length > 0 && (
                <IconButton label={t("sidebar.manageAgents")} onClick={() => { onManageAgents(); onNavigate?.(); }} className="size-7 touch:size-10">
                  <SlidersHorizontal size={14} />
                </IconButton>
              )}
            </SectionHeader>
            {listed.length === 0 && (
              <div className="px-3 pb-2 pt-1">
                <p className="text-xs text-muted">{t("sidebar.noAgents")}</p>
                {onNewAgent && (
                  <Button size="sm" className="mt-2" icon={<Plus size={14} aria-hidden="true" />} onClick={() => { onNewAgent(); onNavigate?.(); }}>
                    {t("sidebar.newAgent")}
                  </Button>
                )}
              </div>
            )}
            <ul className="space-y-0.5">
              {listed.map((agent) => {
                const target = agentTarget(threads, agent.id);
                const isSelected = selectedAgentId === agent.id;
                const status = live.agentId === agent.id ? rowStatus({ working: live.working, waiting: live.waiting }) : null;
                const body = (
                  <>
                    <Avatar agentId={agent.id} name={agent.name} size="md" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{agent.name}</span>
                      {agent.title ? <span className="block truncate text-xs text-muted">{agent.title}</span> : null}
                    </span>
                    <StatusDot status={status} />
                  </>
                );
                const cls = cn(ROW, isSelected ? "bg-hover" : "hover:bg-hover/60");
                return (
                  <li key={agent.id}>
                    {target.kind === "thread" ? (
                      <Link href={`/chat?thread=${target.id}`} onClick={onNavigate} aria-current={isSelected ? "page" : undefined} className={cls}>
                        {body}
                      </Link>
                    ) : (
                      <button type="button" onClick={() => newChat(agent.id)} aria-current={isSelected ? "page" : undefined} className={cls}>
                        {body}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}

        {collaboration}

        <section aria-label={t("sidebar.recent")}>
          <SectionHeader title={t("sidebar.recent")}>
            <IconButton
              label={showArchived ? t("sidebar.hideArchived") : t("sidebar.showArchived")}
              pressed={showArchived}
              onClick={onToggleArchived}
              className="size-7 touch:size-10"
            >
              <Archive size={14} />
            </IconButton>
          </SectionHeader>
          <ul className="space-y-0.5">
            {threads === null && <li className="px-3 py-2 text-xs text-muted">{t("sidebar.loading")}</li>}
            {threads?.length === 0 && (
              <li className="px-3 py-2 text-xs text-muted">{showArchived ? t("sidebar.emptyArchived") : t("sidebar.empty")}</li>
            )}
            {threads?.map((thread) => {
              const open = thread.id === live.threadId;
              return (
                <ThreadRow
                  key={thread.id}
                  thread={thread}
                  selected={thread.id === selectedThreadId}
                  agentName={thread.agent_id != null ? names.get(thread.agent_id) : undefined}
                  status={open ? { working: live.working, waiting: live.waiting } : {}}
                  actions={actions}
                  onNavigate={onNavigate}
                />
              );
            })}
          </ul>
        </section>
      </div>

      {error && <p role="alert" className="px-4 pb-2 text-xs text-danger">{error}</p>}

      <div className="space-y-0.5 border-t border-line p-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        {onOpenRoutines && (
          <button type="button" onClick={() => { onOpenRoutines(); onNavigate?.(); }} className={cn(ROW, "hover:bg-hover/60")}>
            <CalendarClock size={18} aria-hidden="true" className="shrink-0 text-muted" />
            <span className="font-medium">{t("sidebar.routines")}</span>
          </button>
        )}
        <div className="flex items-center gap-1">
          <Link href="/settings/" className={cn(ROW, "flex-1 hover:bg-hover/60")}>
            <Settings size={18} aria-hidden="true" className="shrink-0 text-muted" />
            <span className="font-medium">{t("sidebar.settings")}</span>
          </Link>
          <ThemeToggle iconOnly />
        </div>
      </div>
    </nav>
  );
}
