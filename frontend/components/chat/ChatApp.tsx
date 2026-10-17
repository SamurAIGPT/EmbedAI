"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, isLaunchTokenMissing, type Agent, type LocalStatus, type Thread } from "@/lib/api";
import { activeAgents, agentNames, approvalModeLabel, modelLabel } from "@/lib/agents";
import { pendingApprovals } from "@/lib/transcript";
import { AgentsPanel } from "./AgentsPanel";
import { ApprovalsPanel } from "@/components/approvals/ApprovalsPanel";
import type { Remembered } from "./items";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { ThreadList } from "./ThreadList";
import { useThreadStream } from "./useThreadStream";

const LIST_REFRESH_MS = 15_000;

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
function sendErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === "turn_running") return "Claude is still answering the last message.";
    if (err.code === "session_broken")
      return "Claude can no longer find this conversation. Start a new session to continue.";
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
      <section className="max-w-md rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{children}</div>
        {action && (
          <button
            onClick={action.onClick}
            className="mt-3 cursor-pointer rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
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
  const threadId = parseThreadId(useSearchParams().get("thread"));

  const [gate, setGate] = useState<Gate>({ phase: "loading" });
  const [desktop, setDesktop] = useState(false);
  // Bumped by "Check again": Claude may have been installed or signed in since.
  const [statusTick, setStatusTick] = useState(0);
  const [threads, setThreads] = useState<Thread[] | null>(null);
  // Bumped to ask for a fresh thread list (after a send, a reset, a poll).
  const [listTick, setListTick] = useState(0);
  const [listOpen, setListOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
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
  const [approvalsOpen, setApprovalsOpen] = useState(false);
  // What "Always allow this command" did, by approval id, and rules undone here.
  const [remembered, setRemembered] = useState<Record<string, Remembered>>({});
  const [undoneRules, setUndoneRules] = useState<number[]>([]);
  // The agent a new chat will start with (null: a plain chat).
  const [newAgentId, setNewAgentId] = useState<number | null>(null);

  const stream = useThreadStream(gate.phase === "ready" && gate.status.available ? threadId : null);
  const { transcript } = stream;

  useEffect(() => {
    let cancelled = false;
    api
      .getLocalStatus()
      .then((status) => {
        if (cancelled) return;
        setDesktop(Boolean(window.openDots));
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

  const checkAgain = () => {
    setGate({ phase: "loading" });
    setStatusTick((n) => n + 1);
  };

  const ready = gate.phase === "ready" && gate.status.available;

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

  const thread = threads?.find((t) => t.id === threadId) ?? null;
  const names = agentNames(agents);
  const threadAgent = thread?.agent_id != null ? (agents?.find((a) => a.id === thread.agent_id) ?? null) : null;
  const choosable = activeAgents(agents);
  // The chosen agent may have been archived since: then the chat starts plain.
  const startingAgentId = choosable.some((a) => a.id === newAgentId) ? newAgentId : null;
  const waiting = pendingApprovals(transcript).length > 0;

  let blockedReason: string | null = null;
  if (stream.connection === "failed") blockedReason = "This conversation cannot be opened.";
  else if (thread?.session_state === "broken")
    blockedReason = "Start a new session to continue this conversation.";
  else if (waiting) blockedReason = "Claude is waiting for your answer above.";
  else if (transcript.runningTurnId !== null) blockedReason = "Claude is answering…";

  const send = async () => {
    const text = draft.trim();
    if (!text) return;
    setSendError(null);
    setSending(true);
    try {
      let id = threadId;
      if (id === null) {
        id = (await api.createThread(startingAgentId)).id;
        // Open it now: the stream replays from the start, so nothing is missed.
        router.replace(`/chat?thread=${id}`);
      }
      await api.sendTurn(id, text);
      setDraft("");
    } catch (err) {
      setSendError(sendErrorText(err));
    } finally {
      setSending(false);
      setListTick((n) => n + 1);
    }
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

  const header = (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-zinc-200 px-4 dark:border-zinc-800">
      <button
        onClick={() => setListOpen((open) => !open)}
        aria-label="Conversations"
        className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 md:hidden dark:hover:bg-zinc-900"
      >
        ☰
      </button>
      <Link href={desktop ? "/settings/" : "/"} className="text-sm text-zinc-500 hover:underline">
        {desktop ? "Settings" : "← Home"}
      </Link>
      <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">
        {thread?.title || (threadId === null ? "New chat" : "Chat")}
      </h1>
      {agents !== null &&
        (threadId === null ? (
          <select
            value={startingAgentId ?? ""}
            onChange={(e) => setNewAgentId(e.target.value === "" ? null : Number(e.target.value))}
            aria-label="Agent for this chat"
            className="max-w-40 cursor-pointer truncate rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-xs dark:border-zinc-700"
          >
            <option value="">No agent</option>
            {choosable.map((agent) => (
              <option key={agent.id} value={agent.id}>
                {agent.name}
              </option>
            ))}
          </select>
        ) : (
          threadAgent && (
            <span
              className="hidden max-w-48 truncate text-xs text-zinc-500 sm:inline"
              title={`${modelLabel(threadAgent.model)} · ${approvalModeLabel(threadAgent.approval_mode)}`}
            >
              {threadAgent.name} · {modelLabel(threadAgent.model)}
            </span>
          )
        ))}
      {gate.phase === "ready" && gate.status.available && gate.status.tools_enabled && threadId !== null && (
        <span
          className="hidden rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 sm:inline dark:bg-zinc-900 dark:text-zinc-400"
          title="How much Claude may do before it asks. Set by the conversation's agent; a plain chat asks first."
        >
          {approvalModeLabel(threadAgent?.approval_mode ?? "ask")}
        </span>
      )}
      {gate.phase === "ready" && gate.status.available && (
        <button
          onClick={() => setApprovalsOpen(true)}
          className="cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
        >
          Approvals
        </button>
      )}
      {agents !== null && (
        <button
          onClick={() => setAgentsOpen(true)}
          className="cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
        >
          Agents
        </button>
      )}
      {stream.connection === "reconnecting" && (
        <span className="text-xs text-amber-600 dark:text-amber-400">Reconnecting…</span>
      )}
      {gate.phase === "ready" && gate.status.available && (
        <span className="hidden text-xs text-zinc-500 sm:inline">
          {gate.status.cli_version ?? "Claude Code"}
          {gate.status.tools_enabled ? "" : " · chat only"}
        </span>
      )}
    </header>
  );

  if (gate.phase === "loading") {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <p className="p-6 text-center text-sm text-zinc-500">Loading…</p>
      </div>
    );
  }

  if (gate.phase === "locked") {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <Notice title="Open this from the Open Dots app">
          This address only works inside the Open Dots window, which keeps your conversations
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

  if (!gate.status.available) {
    return (
      <div className="flex h-dvh flex-col">
        {header}
        <Notice
          title={gate.status.enabled ? "Local Claude is not available" : "Local Claude is turned off"}
          action={gate.status.enabled ? { label: "Check again", onClick: checkAgain } : undefined}
        >
          {gate.status.enabled
            ? (gate.status.reason ?? "unknown reason")
            : "Set LOCAL_EXECUTION_ENABLED=true in backend/.env on the machine running the backend, then restart it. Chat runs Claude through your own signed-in CLI on this computer."}
          {desktop && <p className="mt-3"><Link href="/settings/" className="underline">Open Settings</Link> to choose the Claude executable and check its status.</p>}
        </Notice>
      </div>
    );
  }

  return (
    <div className="relative flex h-dvh flex-col">
      {header}
      {approvalsOpen && (
        <ApprovalsPanel
          threadId={threadId}
          workspace={thread?.workspace_path ?? null}
          onClose={() => setApprovalsOpen(false)}
        />
      )}
      {agentsOpen && agents !== null && (
        <AgentsPanel
          agents={agents}
          onClose={() => setAgentsOpen(false)}
          onChanged={() => setAgentTick((n) => n + 1)}
        />
      )}
      <div className="relative flex min-h-0 flex-1">
        <aside
          className={`${
            listOpen ? "flex" : "hidden"
          } absolute inset-y-0 left-0 z-20 w-72 shrink-0 flex-col border-r border-zinc-200 bg-background md:static md:flex dark:border-zinc-800`}
        >
          <ThreadList
            threads={threads}
            selectedId={threadId}
            onNavigate={() => setListOpen(false)}
            showArchived={showArchived}
            onToggleArchived={() => setShowArchived((on) => !on)}
            actions={listActions}
            error={manageError}
            agentNames={names}
          />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          {stream.connection === "failed" ? (
            <Notice title="Cannot open this conversation">{stream.error}</Notice>
          ) : (
            <Conversation
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
            <div className="border-t border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-800 dark:bg-amber-950/30">
              <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-3">
                <p className="min-w-0 flex-1 text-amber-900 dark:text-amber-200">
                  Claude can no longer find this conversation&apos;s session, so it cannot continue it. A new
                  session keeps the messages above but starts without them as context.
                </p>
                <button
                  onClick={resetSession}
                  disabled={resetting}
                  className="cursor-pointer rounded-md bg-zinc-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
                >
                  {resetting ? "Starting…" : "Start a new session"}
                </button>
              </div>
              {resetError && (
                <p className="mx-auto mt-2 max-w-3xl text-xs text-red-600 dark:text-red-400">{resetError}</p>
              )}
            </div>
          )}

          <Composer
            value={draft}
            onChange={setDraft}
            onSend={send}
            sending={sending}
            blockedReason={blockedReason}
            error={sendError}
            running={transcript.runningTurnId !== null}
            stopping={stoppingTurn !== null && stoppingTurn === transcript.runningTurnId}
            onStop={stop}
          />
        </main>
      </div>
    </div>
  );
}
