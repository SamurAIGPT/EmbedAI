"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, isLaunchTokenMissing, type Agent, type Engine, type LocalStatus, type Thread } from "@/lib/api";
import { activeAgents, approvalModeLabel, modelLabel } from "@/lib/agents";
import { pendingApprovals } from "@/lib/transcript";
import { attachmentProblem, conversationUsage, formatCost, formatTokens, MAX_ATTACHMENTS } from "@/lib/everyday";
import { FilesPanel } from "./FilesPanel";
import { UsageView } from "./UsagePanel";
import { SkillsPanel } from "./SkillsPanel";
import { Inspector } from "@/components/shell/Inspector";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { computerChip } from "@/lib/computers";
import { AgentsPanel } from "./AgentsPanel";
import { NotificationBell } from "./NotificationBell";
import { RoutinesPanel } from "./RoutinesPanel";
import { EngineModelFields } from "./EngineModelFields";
import { ComputerView } from "./ComputerPanel";
import { ApprovalsPanel } from "@/components/approvals/ApprovalsPanel";
import type { Remembered } from "./items";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { ChatHeader } from "./ChatHeader";
import { Sidebar } from "@/components/shell/Sidebar";
import { Drawer } from "@/components/ui/Drawer";
import { SIDEBAR_AND_PANEL_FIT, SIDEBAR_INLINE, useMediaQuery } from "@/lib/use-media-query";
import { t } from "@/lib/i18n";
import { reportPresence } from "@/lib/presence";
import { chatHref, parsePanel, type Panel } from "@/lib/panel";
import { parseSkillCommand, skillErrorText, toggleSkill, type SkillSummary } from "@/lib/skills";
import { RoomList } from "./RoomList";
import { RoomView } from "./RoomView";
import { TeamPanel } from "./TeamPanel";
import { parseRoom, roomHref } from "@/lib/rooms";
import { useThreadStream } from "./useThreadStream";

const LIST_REFRESH_MS = 15_000;
const COMPUTER_STATUS_REFRESH_MS = 15_000;

type Gate =
  | { phase: "loading" }
  | { phase: "unreachable" }
  // The backend wants the desktop app's launch token: this page is not in the app's window.
  | { phase: "locked" }
  | { phase: "ready"; status: LocalStatus };

function parseThreadId(raw: string | null): number | null {
  return raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
}

// What went wrong with a rename, archive or delete, in words a person can act on.
function manageErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "turn_running") return "Stop the running answer first.";
    return err.message || "That did not work.";
  }
  return "Could not reach the backend.";
}

// Says what went wrong in words a person can act on.
// A typed `/name` adds that skill to the ones already picked, within the limit.
function toggleSkillOn(chosen: string[], name: string): string[] {
  return chosen.includes(name) ? chosen : toggleSkill(chosen, name);
}

function sendErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "skill_unavailable") return skillErrorText(err);
    if (err.code === "turn_running") return "An answer to the last message is still running.";
    if (err.code === "session_broken")
      return "The engine can no longer find this conversation. Start a new session to continue.";
    return err.message || "Could not send the message.";
  }
  return "Could not reach the backend.";
}

function Notice({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <section className="max-w-md rounded-card border border-line bg-raised p-5">
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="mt-1 text-sm text-muted">{children}</div>
        {action && (
          <button
            onClick={action.onClick}
            className="mt-3 cursor-pointer rounded-field border border-line px-3 py-1.5 text-sm font-medium hover:bg-hover"
          >
            {action.label}
          </button>
        )}
      </section>
    </div>
  );
}

// The chat: a list of conversations and one conversation, on the durable threads
// API. The page keeps nothing that matters: every conversation lives in the
// backend, and the open one is replayed from its stored events.
export function ChatApp() {
  const router = useRouter();
  const params = useSearchParams();
  const threadId = parseThreadId(params.get("thread"));
  const roomId = parseRoom(params.get("room"));
  const [teamOpen, setTeamOpen] = useState(false);

  const [gate, setGate] = useState<Gate>({ phase: "loading" });
  // Bumped by "Check again": Claude may have been installed or signed in since.
  const [statusTick, setStatusTick] = useState(0);
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [openedThread, setOpenedThread] = useState<Thread | null>(null);
  // Bumped to ask for a fresh thread list (after a send, a reset, a poll).
  const [listTick, setListTick] = useState(0);
  // Below the sidebar breakpoint the conversation list is a drawer.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const sidebarInline = useMediaQuery(SIDEBAR_INLINE);
  const inspectorInline = useMediaQuery(SIDEBAR_AND_PANEL_FIT);
  // The right-hand inspector page, kept in the address (?panel=usage) so a reload reopens it.
  const panel = parsePanel(params.get("panel"));
  const setPanel = (next: Panel | null) => router.replace(chatHref(params.toString(), next));
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [focusComposer, setFocusComposer] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [answering, setAnswering] = useState<string | null>(null);
  const [approvalErrors, setApprovalErrors] = useState<Record<string, string>>({});
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [manageError, setManageError] = useState<string | null>(null);
  // The turn Stop was pressed for; cleared by the turn ending, which is what
  // the button waits for.
  const [stoppingTurn, setStoppingTurn] = useState<number | null>(null);
  // Named agents. null: the backend has none to offer (an older one answers 404),
  // and the page behaves as before.
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [agentTick, setAgentTick] = useState(0);
  const [agentsOpen, setAgentsOpen] = useState(false);
  // The Agents panel opened from New agent starts on the form.
  const [agentsCreate, setAgentsCreate] = useState(false);
  const [routinesOpen, setRoutinesOpen] = useState(false);
  const [approvalsOpen, setApprovalsOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  // Files chosen for the next message, and why one was refused.
  const [attached, setAttached] = useState<File[]>([]);
  const [attachNotice, setAttachNotice] = useState<string | null>(null);
  // Skills picked for the next message only, and the text a Save as skill opened the editor with.
  // Both are tied to the agent they were made for, so another agent starts clean.
  const [picked, setPicked] = useState<{ agentId: number | null; names: string[] }>({ agentId: null, names: [] });
  const [skillSeed, setSkillSeed] = useState<{ text: string; nonce: number } | null>(null);
  const [loadedSkills, setLoadedSkills] = useState<{ agentId: number; list: SkillSummary[] } | null>(null);
  // What "Always allow this command" did, by approval id, and rules undone here.
  const [remembered, setRemembered] = useState<Record<string, Remembered>>({});
  const [undoneRules, setUndoneRules] = useState<number[]>([]);
  // The agent a new chat will start with (null: a plain chat).
  const [newAgentId, setNewAgentId] = useState<number | null>(() => parseThreadId(params.get("agent")));
  const [engines, setEngines] = useState<Engine[] | null>(null);
  const [newProvider, setNewProvider] = useState(() => params.get("engine") || "claude");
  const [newModel, setNewModel] = useState<string | null>(() => params.get("model") || null);
  const [modelDraft, setModelDraft] = useState<{ threadId: number | null; agentId: number | null; provider: string; model: string | null } | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);

  // Engine discovery also grants access to history and setup when Claude is
  // absent. Starting a turn is checked against the selected engine below.
  const ready = gate.phase === "ready" && (gate.status.available || (gate.status.enabled && engines !== null));
  const stream = useThreadStream(ready ? threadId : null);
  const { transcript } = stream;

  useEffect(() => {
    if (!window.openDots) return;
    let cancelled = false;
    api.getDesktopSettings().then((value) => {
      if (!cancelled && value.settings.some((row) => row.key === "DESKTOP_ONBOARDING_COMPLETED" && row.value === false)) router.replace("/settings/");
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [router]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getLocalStatus(), api.getEngines().catch(() => null)])
      .then(([status, items]) => {
        if (cancelled) return;
        setEngines(items);
        setGate({ phase: "ready", status });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (isLaunchTokenMissing(err)) return setGate({ phase: "locked" });
        // An answer that is an error (an older backend without the route) is not "unreachable".
        setGate(err instanceof ApiError ? { phase: "ready", status: { enabled: false, available: false, reason: null, cli_version: null } } : { phase: "unreachable" });
      });
    return () => {
      cancelled = true;
    };
  }, [statusTick]);

  // Tell the server the person is here (it stores their time zone and writes
  // today's Needs you card). It names the card's chat only on a return or the
  // first visit of the day, and never moves a chat that is already open.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    const ping = () => {
      if (document.visibilityState === "hidden") return;
      reportPresence()
        .then((result) => {
          if (!cancelled && result.open && !window.location.search.match(/[?&](thread|room)=/)) router.push(`/chat?thread=${result.open.thread_id}`);
        })
        .catch(() => {
          // Not the owner, or an older backend: nothing to show.
        });
    };
    ping();
    document.addEventListener("visibilitychange", ping);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", ping);
    };
  }, [ready, router]);

  const checkAgain = () => {
    setGate({ phase: "loading" });
    setStatusTick((n) => n + 1);
  };

  // The list is read whenever the tick changes, and again when a turn starts or
  // ends (that changes the order, the title and the session state).
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api
      .listThreads(showArchived)
      .then((list) => !cancelled && setThreads(list))
      .catch(() => {
        // keep what is shown; the next refresh tries again
      });
    return () => {
      cancelled = true;
    };
  }, [ready, listTick, showArchived, transcript.runningTurnId]);

  // A bookmarked archived conversation may be absent from the sidebar. Its
  // engine and model still come from the conversation, never new-chat defaults.
  useEffect(() => {
    if (!ready || threadId === null) return;
    let cancelled = false;
    api.getThread(threadId).then((value) => { if (!cancelled) setOpenedThread(value); }).catch((error: unknown) => {
      // A deleted conversation can remain in local state until the next list
      // poll. Clear its cached agent/computer metadata on a definite 404.
      if (!cancelled && error instanceof ApiError && error.status === 404) {
        setOpenedThread((current) => current?.id === threadId ? null : current);
        setThreads((current) => current?.filter((item) => item.id !== threadId) ?? current);
      }
    });
    return () => { cancelled = true; };
  }, [ready, threadId, listTick, transcript.runningTurnId]);

  useEffect(() => {
    const closeModel = () => setModelDraft(null);
    window.addEventListener("popstate", closeModel);
    return () => window.removeEventListener("popstate", closeModel);
  }, []);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api
      .listAgents(true)
      .then((list) => !cancelled && setAgents(list))
      .catch((err: unknown) => {
        // 404: an older backend without agents. Anything else: keep what is shown.
        if (!cancelled && err instanceof ApiError && err.status === 404) setAgents(null);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, agentTick]);

  useEffect(() => {
    if (!ready) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") setListTick((n) => n + 1);
    }, LIST_REFRESH_MS);
    return () => clearInterval(timer);
  }, [ready]);

  const thread = (openedThread?.id === threadId ? openedThread : null) ?? threads?.find((t) => t.id === threadId) ?? null;
  const threadAgent = thread?.agent_id != null ? (agents?.find((a) => a.id === thread.agent_id) ?? null) : null;
  // The computer this conversation's tools run in; older backends send none.
  const threadComputer = thread?.computer ?? threadAgent?.computer ?? null;
  const [liveComputer, setLiveComputer] = useState<{ id: number; state: string } | null>(null);
  const liveComputerState = liveComputer && liveComputer.id === threadComputer?.id ? liveComputer.state : null;

  useEffect(() => {
    if (!ready || !threadAgent || !threadComputer) return;
    let cancelled = false;
    const refreshComputer = async () => {
      try {
        const computer = await api.getAgentComputer(threadAgent.id);
        if (!cancelled) setLiveComputer({ id: computer.id, state: computer.live_state ?? computer.state });
      } catch {
        if (!cancelled) setLiveComputer(null);
      }
    };
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refreshComputer();
    };
    void refreshComputer();
    document.addEventListener("visibilitychange", refreshWhenVisible);
    const timer = setInterval(() => {
      refreshWhenVisible();
    }, COMPUTER_STATUS_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [ready, threadAgent?.id, threadComputer?.id]);

  const choosable = activeAgents(agents);
  // The chosen agent may have been archived since: then the chat starts plain.
  const startingAgentId = choosable.some((a) => a.id === newAgentId) ? newAgentId : null;
  const startingAgent = choosable.find((agent) => agent.id === startingAgentId);
  const selectedProvider = thread?.provider ?? startingAgent?.provider ?? newProvider;
  const selectedEngine = engines?.find((engine) => engine.id === selectedProvider);
  const selectedAgent = threadId === null ? startingAgent : threadAgent?.provider === thread?.provider ? threadAgent : null;
  const selectedModel = (threadId === null ? newModel : thread?.model) ?? selectedAgent?.model ?? null;
  const skillAgent = threadId === null ? startingAgent : threadAgent;
  const skillAgentId = skillAgent?.id ?? null;
  const [skillsTick, setSkillsTick] = useState(0);
  useEffect(() => {
    if (!ready || skillAgentId === null) return;
    let live = true;
    api.listSkills(skillAgentId).then(
      (list) => live && setLoadedSkills({ agentId: skillAgentId, list }),
      () => live && setLoadedSkills({ agentId: skillAgentId, list: [] }),
    );
    return () => { live = false; };
  }, [ready, skillAgentId, skillsTick]);
  const agentSkills = loadedSkills !== null && loadedSkills.agentId === skillAgentId ? loadedSkills.list : [];
  const chosenSkills = picked.agentId === skillAgentId ? picked.names.filter((n) => agentSkills.some((s) => s.name === n && s.enabled)) : [];
  const setChosenSkills = (update: (names: string[]) => string[]) =>
    setPicked({ agentId: skillAgentId, names: update(chosenSkills) });
  // The last thing the person asked in this conversation: the seed for Save as skill.
  const lastRequest = [...transcript.items].reverse().find((item) => item.kind === "user");
  const canSaveSkill = threadAgent !== null && lastRequest?.kind === "user" && transcript.runningTurnId === null;
  const waiting = pendingApprovals(transcript).length > 0;
  const totals = conversationUsage(
    transcript.items.flatMap((item) => (item.kind === "turn_end" ? [{ usage: item.usage }] : []))
  );
  const conversationTotal =
    totals.turns === 0
      ? "Tokens and cost by month"
      : `This conversation: ${formatTokens(totals.tokens)} tokens, ${formatCost(totals.cost)}` +
        (totals.reported < totals.turns ? ` (${totals.turns - totals.reported} turn(s) reported nothing)` : "");

  let blockedReason: string | null = null;
  if (stream.connection === "failed") blockedReason = "This conversation cannot be opened.";
  else if (threadId !== null && thread === null) blockedReason = "Loading conversation…";
  else if (thread?.session_state === "broken")
    blockedReason = "Start a new session to continue this conversation.";
  else if (waiting) blockedReason = "The engine is waiting for your answer above.";
  else if (transcript.runningTurnId !== null) blockedReason = "An answer is running…";
  else if (selectedEngine && !selectedEngine.available) blockedReason = selectedEngine.reason ?? "This engine is unavailable. Check its settings.";

  const imagesAttached = attached.some((file) => file.type.startsWith("image/"));
  const imagesRefused = imagesAttached && selectedEngine !== undefined && !selectedEngine.capabilities.images;
  if (blockedReason === null && imagesRefused)
    blockedReason = `${selectedEngine?.label ?? "This engine"} can't read images. Remove the image to send.`;

  const addFiles = (files: File[]) => {
    const problems: string[] = [];
    const accepted = files.filter((file) => {
      const problem = attachmentProblem(file);
      if (problem) problems.push(problem);
      return problem === null;
    });
    const room = Math.max(0, MAX_ATTACHMENTS - attached.length);
    if (accepted.length > room) problems.push(`Attach at most ${MAX_ATTACHMENTS} files to one message.`);
    setAttached((previous) => [...previous, ...accepted.slice(0, room)]);
    setAttachNotice(problems.length ? problems.join(" ") : null);
  };

  const send = async () => {
    // `/name rest` for a skill that is on selects it; anything else is sent as typed.
    const command = parseSkillCommand(draft, agentSkills.filter((s) => s.enabled).map((s) => s.name));
    const usedSkills = command ? toggleSkillOn(chosenSkills, command.name) : chosenSkills;
    const message = command ? command.rest : draft.trim();
    const text = message;
    if (!text) return;
    setSendError(null);
    setSending(true);
    try {
      let id = threadId;
      if (id === null) {
        id = (await api.createThread(startingAgentId, startingAgentId !== null ? { model: newModel } : { provider: newProvider, model: newModel })).id;
        // Open it now: the stream replays from the start, so nothing is missed.
        router.replace(`/chat?thread=${id}`);
      }
      const ids: string[] = [];
      for (const file of attached) ids.push((await api.uploadAttachment(id, file)).id);
      await api.sendTurn(id, message, ids, usedSkills);
      setDraft("");
      setChosenSkills(() => []);
      setAttached([]);
      setAttachNotice(null);
    } catch (err) {
      setSendError(sendErrorText(err));
      if (err instanceof ApiError && err.code === "skill_unavailable") setSkillsTick((n) => n + 1);
    } finally {
      setSending(false);
      setListTick((n) => n + 1);
    }
  };

  const saveModel = async () => {
    if (!modelDraft) return;
    const target = modelDraft;
    setModelBusy(true); setModelError(null);
    const model = target.model?.trim() || null;
    try {
      if (target.threadId !== null) {
        const updated = await api.updateThread(target.threadId, { model });
        setThreads((previous) => previous?.map((item) => item.id === target.threadId ? updated : item) ?? null);
        setOpenedThread((previous) => previous?.id === target.threadId ? updated : previous);
      } else {
        setNewProvider(target.provider);
        setNewModel(model);
      }
      setModelDraft((previous) => previous === target ? null : previous);
    } catch (err) { setModelError(err instanceof ApiError ? err.message : "Could not change the model."); }
    finally { setModelBusy(false); }
  };

  const stop = async () => {
    if (threadId === null || transcript.runningTurnId === null) return;
    setSendError(null);
    setStoppingTurn(transcript.runningTurnId);
    try {
      await api.stopThread(threadId);
    } catch (err) {
      // Already over: the stream says so, nothing to show.
      if (!(err instanceof ApiError && err.code === "no_running_turn")) {
        setSendError(err instanceof ApiError ? err.message : "Could not stop the answer.");
      }
      setStoppingTurn(null);
    }
  };

  // One place for rename, archive and delete: say what went wrong, and always
  // read the list again afterwards.
  const manage = async (change: () => Promise<void>) => {
    setManageError(null);
    try {
      await change();
    } catch (err) {
      setManageError(manageErrorText(err));
    } finally {
      setListTick((n) => n + 1);
    }
  };

  const listActions = {
    onRename: (id: number, title: string) => manage(() => api.updateThread(id, { title }).then(() => {})),
    onArchive: (id: number, archived: boolean) =>
      manage(() => api.updateThread(id, { archived }).then(() => {})),
    onDelete: (id: number) =>
      manage(async () => {
        await api.deleteThread(id);
        if (id === threadId) router.replace("/chat");
      }),
  };

  const answer = async (approvalId: string, decision: "allow" | "deny", remember = false) => {
    if (threadId === null) return;
    setAnswering(approvalId);
    setApprovalErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== approvalId)));
    try {
      const result = await api.answerThreadApproval(threadId, approvalId, decision, remember);
      if (remember && decision === "allow") {
        setRemembered((prev) => ({
          ...prev,
          [approvalId]: { ruleId: result?.rule_id ?? null, error: result?.remember_error ?? null },
        }));
      }
    } catch (err) {
      // Not waiting any more (answered elsewhere, expired, the run ended): the
      // stream already says so, and the card updates by itself.
      if (!(err instanceof ApiError && err.code === "approval_not_waiting")) {
        const message = err instanceof ApiError ? err.message : "Could not send your answer.";
        setApprovalErrors((prev) => ({ ...prev, [approvalId]: message }));
      }
    } finally {
      setAnswering(null);
    }
  };

  // Take back an "always allow": the rule is removed and logged as undone.
  const undoRule = async (ruleId: number) => {
    try {
      await api.deleteApprovalRule(ruleId, threadId);
    } catch (err) {
      // 404: already removed (from the rules list, say). Anything else is shown.
      if (!(err instanceof ApiError && err.status === 404)) {
        setManageError(err instanceof ApiError ? err.message : "Could not remove the rule.");
        return;
      }
    }
    setUndoneRules((prev) => [...prev, ruleId]);
  };

  const resetSession = async () => {
    if (threadId === null) return;
    setResetError(null);
    setResetting(true);
    try {
      await api.resetThreadSession(threadId);
    } catch (err) {
      setResetError(err instanceof ApiError ? err.message : "Could not start a new session.");
    } finally {
      setResetting(false);
      setListTick((n) => n + 1);
    }
  };

  const openModelDialog = () => {
    setModelError(null);
    setModelDraft({ threadId, agentId: startingAgentId, provider: selectedProvider, model: threadId === null ? newModel : thread?.model ?? null });
  };

  const computerText = threadComputer ? computerChip(threadComputer, liveComputerState) : null;
  const buildHeader = (withSidebar: boolean) => (
    <ChatHeader
      title={thread?.title || (threadId === null ? t("shell.newChat") : "Chat")}
      agent={threadAgent ? { id: threadAgent.id, name: threadAgent.name } : null}
      onOpenSidebar={withSidebar && !sidebarInline ? () => setDrawerOpen(true) : undefined}
      agentChoice={
        agents !== null && threadId === null
          ? { agents: choosable, value: startingAgentId, onChange: (id) => { setNewAgentId(id); setNewModel(null); setModelDraft(null); } }
          : undefined
      }
      engine={
        ready && engines && (threadId === null || thread !== null)
          ? {
              label: `${selectedEngine?.label ?? selectedProvider} · ${modelLabel(selectedModel)}`,
              disabled: modelBusy || sending || transcript.runningTurnId !== null,
              onClick: openModelDialog,
            }
          : undefined
      }
      computer={
        threadComputer && threadAgent && computerText
          ? {
              text: computerText,
              hint: `This conversation's commands and file edits run in ${threadAgent.name}'s computer (${threadComputer.image}), not on this machine.`,
              onClick: () => setPanel("computer"),
            }
          : undefined
      }
      approvalMode={
        ready && gate.phase === "ready" && (gate.status.tools_enabled || threadComputer) && threadId !== null
          ? {
              label: approvalModeLabel(threadAgent?.approval_mode ?? "ask"),
              hint: "How much the engine may do before it asks. Set by the conversation's agent; a plain chat asks first.",
            }
          : undefined
      }
      onApprovals={ready ? () => setApprovalsOpen(true) : undefined}
      onFiles={ready && threadId !== null && thread !== null ? () => setFilesOpen(true) : undefined}
      onUsage={ready ? () => setPanel("usage") : undefined}
      usageHint={conversationTotal}
      trailing={
        ready ? (
          <NotificationBell
            elevated={routinesOpen || panel === "usage"}
            onOpen={() => {
              setRoutinesOpen(false);
              if (panel === "usage") setPanel(null);
            }}
          />
        ) : undefined
      }
      status={
        stream.connection === "reconnecting"
          ? { text: t("header.reconnecting"), warn: true }
          : ready && selectedEngine
            ? { text: `${selectedEngine.available ? selectedEngine.version ?? t("header.ready") : t("header.unavailable")}${selectedEngine.capabilities.tools ? "" : ` · ${t("header.chatOnly")}`}` }
            : null
      }
    />
  );
  const header = buildHeader(false);

  const sidebar = (
    <Sidebar
      agents={agents}
      threads={threads}
      selectedThreadId={threadId}
      selectedAgentId={thread?.agent_id ?? (threadId === null ? startingAgentId : null)}
      live={{ threadId, agentId: thread?.agent_id ?? null, working: transcript.runningTurnId !== null, waiting }}
      showArchived={showArchived}
      onToggleArchived={() => setShowArchived((on) => !on)}
      actions={listActions}
      error={manageError}
      onNewChat={(agentId) => {
        if (agentId !== undefined) { setNewAgentId(agentId); setNewModel(null); }
        setModelDraft(null);
        setFocusComposer(true);
        router.push("/chat");
      }}
      onManageAgents={agents !== null ? () => { setAgentsCreate(false); setAgentsOpen(true); } : undefined}
      onNewAgent={agents !== null ? () => { setAgentsCreate(true); setAgentsOpen(true); } : undefined}
      collaboration={ready ? <RoomList selected={roomId} onTeam={() => setTeamOpen(true)} onNavigate={() => setDrawerOpen(false)} /> : undefined}
      onOpenRoutines={ready && agents !== null ? () => setRoutinesOpen(true) : undefined}
      onNavigate={() => { setDrawerOpen(false); setModelDraft(null); }}
    />
  );

  if (gate.phase === "loading") {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <p className="p-6 text-center text-sm text-muted">Loading…</p>
      </div>
    );
  }

  if (gate.phase === "locked") {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <Notice title="Open this from the Open Duet app">
          This address only works inside the Open Duet window, which keeps your conversations
          private to it. Use that window instead of a browser tab.
        </Notice>
      </div>
    );
  }

  if (gate.phase === "unreachable") {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <Notice title="Backend not reachable">
          Check that it is running, and that this page&apos;s address (
          {typeof window === "undefined" ? "" : window.location.origin}) is listed in
          FRONTEND_ORIGINS in backend/.env.
        </Notice>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <Notice
          title={gate.status.enabled ? "Engines are not available" : "Local execution is turned off"}
          action={gate.status.enabled ? { label: "Check again", onClick: checkAgain } : undefined}
        >
          {gate.status.enabled
            ? (gate.status.reason ?? "unknown reason")
            : "Set LOCAL_EXECUTION_ENABLED=true in backend/.env on the machine running the backend, then restart it."}
          <p className="mt-3"><Link href="/settings/" className="underline">Open Settings</Link> to configure an engine and check its status.</p>
        </Notice>
      </div>
    );
  }

  return (
    <div className="relative flex h-dvh">
      <a
        href="#conversation"
        className="sr-only z-50 rounded-field bg-raised px-3 py-2 text-sm font-medium text-ink shadow-lg focus:not-sr-only focus:absolute focus:left-3 focus:top-3"
      >
        {t("shell.skip")}
      </a>
      {sidebarInline ? (
        <aside className="w-72 shrink-0 border-r border-line">{sidebar}</aside>
      ) : (
        <Drawer open={drawerOpen} onOpenChange={setDrawerOpen} label={t("shell.conversations")}>{sidebar}</Drawer>
      )}
      <div className="relative flex min-w-0 flex-1 flex-col">
      {buildHeader(true)}
      {modelDraft && modelDraft.threadId === threadId && (threadId !== null || modelDraft.agentId === startingAgentId) && engines && (
        <Dialog open onOpenChange={(open) => { if (!open) setModelDraft(null); }} title="Engine and model" size="sm">
          <form onSubmit={(event) => { event.preventDefault(); void saveModel(); }} className="space-y-4">
            <EngineModelFields engines={engines} provider={modelDraft.provider} model={modelDraft.model} onChange={(provider, model) => setModelDraft({ ...modelDraft, provider, model })} disabled={modelBusy} lockProvider={threadId !== null || startingAgentId !== null} defaultLabel={selectedAgent ? `Agent default (${modelLabel(selectedAgent.model)})` : undefined} />
            {threadId !== null && <p className="text-xs text-muted">The model changes on the next message. This conversation keeps its engine.</p>}
            {modelError && <p role="alert" className="text-sm text-danger">{modelError}</p>}
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" variant="primary" disabled={modelBusy}>{modelBusy ? "Saving…" : "Use model"}</Button>
              <Button disabled={modelBusy} onClick={() => setModelDraft(null)}>Cancel</Button>
              <Button variant="ghost" disabled={modelBusy} onClick={() => { setModelBusy(true); api.getEngines(true).then(setEngines).catch(() => setModelError("Could not refresh engines.")).finally(() => setModelBusy(false)); }}>Refresh status</Button>
            </div>
          </form>
        </Dialog>
      )}
      {filesOpen && threadId !== null && (
        <FilesPanel threadId={threadId} workspace={thread?.workspace_path ?? null} onClose={() => setFilesOpen(false)} />
      )}
      {approvalsOpen && (
        <ApprovalsPanel
          threadId={threadId}
          workspace={thread?.workspace_path ?? null}
          onClose={() => setApprovalsOpen(false)}
        />
      )}
      {teamOpen && agents !== null && <TeamPanel agents={agents} onClose={() => setTeamOpen(false)} onChanged={() => setAgentTick(value => value + 1)} />}
      {roomId !== null && agents !== null && <RoomView key={roomId} roomId={roomId} agents={agents} onClose={() => router.push("/chat")} onSelect={id => router.replace(roomHref(id))} />}
      {routinesOpen && agents !== null && (
        <RoutinesPanel agents={agents} onClose={() => setRoutinesOpen(false)} />
      )}
      {agentsOpen && agents !== null && (
        <AgentsPanel
          agents={agents}
          startCreating={agentsCreate}
          onClose={() => setAgentsOpen(false)}
          onChanged={() => setAgentTick((n) => n + 1)}
          onCreated={(agent) => setNewAgentId(agent.id)}
        />
      )}
        <main id="conversation" tabIndex={-1} className="flex min-h-0 min-w-0 flex-1 flex-col outline-none">
          {stream.connection === "failed" ? (
            <Notice title="Cannot open this conversation">{stream.error}</Notice>
          ) : (
            <Conversation
              threadId={threadId}
              agent={threadAgent ? { id: threadAgent.id, name: threadAgent.name } : startingAgent ? { id: startingAgent.id, name: startingAgent.name } : null}
              emptyHint={
                selectedEngine && !selectedEngine.available
                  ? `${selectedEngine.label} is not ready: ${(selectedEngine.reason ?? "check it in Settings").replace(/\.+$/, "")}.`
                  : undefined
              }
              transcript={transcript}
              answering={answering}
              approvalErrors={approvalErrors}
              onAnswer={answer}
              remembered={remembered}
              undoneRules={undoneRules}
              onUndo={undoRule}
            />
          )}

          {thread?.session_state === "broken" && (
            <div className="border-t border-warning/50 bg-warning/10 px-4 py-3 text-sm">
              <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-3">
                <p className="min-w-0 flex-1 text-ink">
                  The engine can no longer find this conversation&apos;s session, so it cannot continue it. A new
                  session keeps the messages above but starts without them as context.
                </p>
                <Button variant="primary" onClick={resetSession} loading={resetting}>
                  {resetting ? "Starting…" : "Start a new session"}
                </Button>
              </div>
              {resetError && <p className="mx-auto mt-2 max-w-3xl text-xs text-danger">{resetError}</p>}
            </div>
          )}

          <Composer
            focusRequested={focusComposer}
            onFocused={() => setFocusComposer(false)}
            value={draft}
            onChange={setDraft}
            onSend={send}
            sending={sending}
            blockedReason={blockedReason}
            error={sendError}
            running={transcript.runningTurnId !== null}
            stopping={stoppingTurn !== null && stoppingTurn === transcript.runningTurnId}
            onStop={stop}
            files={attached}
            onAddFiles={addFiles}
            onRemoveFile={(index) => {
              setAttached((previous) => previous.filter((_, i) => i !== index));
              setAttachNotice(null);
            }}
            attachNotice={attachNotice}
            skills={skillAgentId !== null ? agentSkills : undefined}
            chosenSkills={chosenSkills}
            onToggleSkill={skillAgentId !== null ? (name) => setChosenSkills((names) => toggleSkill(names, name)) : undefined}
            onSaveAsSkill={canSaveSkill ? () => { setSkillSeed({ text: lastRequest.text, nonce: Date.now() }); setPanel("skills"); } : undefined}
          />
        </main>
      </div>
      {panel === "usage" && (
        <Inspector title="Usage" inline={inspectorInline} onClose={() => setPanel(null)}>
          <UsageView />
        </Inspector>
      )}
      {panel === "skills" && threadAgent && (
        <Inspector title={`Skills · ${threadAgent.name}`} inline={inspectorInline} onClose={() => { setSkillSeed(null); setPanel(null); }}>
          <div className="p-4">
            <SkillsPanel key={`${threadAgent.id}-${skillSeed?.nonce ?? 0}`} agent={threadAgent} seed={skillSeed?.text ?? null} onChanged={() => setSkillsTick((n) => n + 1)} />
          </div>
        </Inspector>
      )}
      {panel === "computer" && threadAgent && (
        <Inspector title={`Computer · ${threadAgent.name}`} inline={inspectorInline} onClose={() => setPanel(null)}>
          <ComputerView
            agent={threadAgent}
            onChanged={() => setAgentTick((n) => n + 1)}
            engineSupportsComputers={engines?.find((engine) => engine.id === threadAgent.provider)?.capabilities.computers}
            engineLabel={engines?.find((engine) => engine.id === threadAgent.provider)?.label ?? threadAgent.provider}
          />
        </Inspector>
      )}
    </div>
  );
}
