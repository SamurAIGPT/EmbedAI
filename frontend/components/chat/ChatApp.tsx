"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, isLaunchTokenMissing, type Agent, type Engine, type LocalStatus, type Thread } from "@/lib/api";
import { activeAgents, agentNames, approvalModeLabel, modelLabel } from "@/lib/agents";
import { pendingApprovals } from "@/lib/transcript";
import { computerChip } from "@/lib/computers";
import { AgentsPanel } from "./AgentsPanel";
import { EngineModelFields } from "./EngineModelFields";
import { ComputerView } from "./ComputerPanel";
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
  // Bumped by "Check again": Claude may have been installed or signed in since.
  const [statusTick, setStatusTick] = useState(0);
  const [threads, setThreads] = useState<Thread[] | null>(null);
  const [openedThread, setOpenedThread] = useState<Thread | null>(null);
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
  const [computerOpen, setComputerOpen] = useState(false);
  const [approvalsOpen, setApprovalsOpen] = useState(false);
  // What "Always allow this command" did, by approval id, and rules undone here.
  const [remembered, setRemembered] = useState<Record<string, Remembered>>({});
  const [undoneRules, setUndoneRules] = useState<number[]>([]);
  // The agent a new chat will start with (null: a plain chat).
  const [newAgentId, setNewAgentId] = useState<number | null>(null);
  const [engines, setEngines] = useState<Engine[] | null>(null);
  const [newProvider, setNewProvider] = useState("claude");
  const [newModel, setNewModel] = useState<string | null>(null);
  const [modelDraft, setModelDraft] = useState<{ threadId: number | null; agentId: number | null; provider: string; model: string | null } | null>(null);
  const [modelBusy, setModelBusy] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);

  // Engine discovery also grants access to history and setup when Claude is
  // absent. Starting a turn is checked against the selected engine below.
  const ready = gate.phase === "ready" && (gate.status.available || (gate.status.enabled && engines !== null));
  const stream = useThreadStream(ready ? threadId : null);
  const { transcript } = stream;

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
    api.getThread(threadId).then((value) => { if (!cancelled) setOpenedThread(value); }).catch(() => {});
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
  const names = agentNames(agents);
  const threadAgent = thread?.agent_id != null ? (agents?.find((a) => a.id === thread.agent_id) ?? null) : null;
  // The computer this conversation's tools run in; older backends send none.
  const threadComputer = thread?.computer ?? threadAgent?.computer ?? null;
  const choosable = activeAgents(agents);
  // The chosen agent may have been archived since: then the chat starts plain.
  const startingAgentId = choosable.some((a) => a.id === newAgentId) ? newAgentId : null;
  const startingAgent = choosable.find((agent) => agent.id === startingAgentId);
  const selectedProvider = thread?.provider ?? startingAgent?.provider ?? newProvider;
  const selectedEngine = engines?.find((engine) => engine.id === selectedProvider);
  const selectedAgent = threadId === null ? startingAgent : threadAgent?.provider === thread?.provider ? threadAgent : null;
  const selectedModel = (threadId === null ? newModel : thread?.model) ?? selectedAgent?.model ?? null;
  const waiting = pendingApprovals(transcript).length > 0;

  let blockedReason: string | null = null;
  if (stream.connection === "failed") blockedReason = "This conversation cannot be opened.";
  else if (threadId !== null && thread === null) blockedReason = "Loading conversation…";
  else if (thread?.session_state === "broken")
    blockedReason = "Start a new session to continue this conversation.";
  else if (waiting) blockedReason = "The engine is waiting for your answer above.";
  else if (transcript.runningTurnId !== null) blockedReason = "An answer is running…";
  else if (selectedEngine && !selectedEngine.available) blockedReason = selectedEngine.reason ?? "This engine is unavailable. Check its settings.";

  const send = async () => {
    const text = draft.trim();
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
      await api.sendTurn(id, text);
      setDraft("");
    } catch (err) {
      setSendError(sendErrorText(err));
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

  const header = (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-zinc-200 px-4 dark:border-zinc-800">
      <button
        onClick={() => setListOpen((open) => !open)}
        aria-label="Conversations"
        className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 md:hidden dark:hover:bg-zinc-900"
      >
        ☰
      </button>
      <Link href="/settings/" className="text-sm text-zinc-500 hover:underline">
        Settings
      </Link>
      <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">
        {thread?.title || (threadId === null ? "New chat" : "Chat")}
      </h1>
      {agents !== null &&
        (threadId === null ? (
          <select
            value={startingAgentId ?? ""}
            onChange={(e) => { setNewAgentId(e.target.value === "" ? null : Number(e.target.value)); setNewModel(null); setModelDraft(null); }}
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
              {threadAgent.name}
            </span>
          )
        ))}
      {ready && engines && (threadId === null || thread !== null) && <button aria-label="Choose engine and model" disabled={modelBusy || sending || transcript.runningTurnId !== null} onClick={() => { setModelError(null); setModelDraft({ threadId, agentId: startingAgentId, provider: selectedProvider, model: threadId === null ? newModel : thread?.model ?? null }); }} className="max-w-52 cursor-pointer truncate rounded-md border border-zinc-300 px-2 py-1 text-xs disabled:opacity-50 dark:border-zinc-700">{selectedEngine?.label ?? selectedProvider} · {modelLabel(selectedModel)}</button>}
      {threadComputer && threadAgent && (
        <button
          onClick={() => setComputerOpen(true)}
          title={`This conversation's commands and file edits run in ${threadAgent.name}'s computer (${threadComputer.image}), not on this machine.`}
          className="cursor-pointer rounded-md border border-sky-300 bg-sky-50 px-2 py-1 text-xs text-sky-800 hover:bg-sky-100 dark:border-sky-800 dark:bg-sky-950 dark:text-sky-300"
        >
          {computerChip(threadComputer)}
        </button>
      )}
      {ready && gate.phase === "ready" && (gate.status.tools_enabled || threadComputer) && threadId !== null && (
        <span
          className="hidden rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600 sm:inline dark:bg-zinc-900 dark:text-zinc-400"
          title="How much the engine may do before it asks. Set by the conversation's agent; a plain chat asks first."
        >
          {approvalModeLabel(threadAgent?.approval_mode ?? "ask")}
        </span>
      )}
      {ready && (
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
      {ready && selectedEngine && (
        <span className="hidden text-xs text-zinc-500 sm:inline">
          {selectedEngine.available ? selectedEngine.version ?? "Ready" : "Unavailable"}
          {selectedEngine.capabilities.tools ? "" : " · chat only"}
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
    <div className="relative flex h-dvh flex-col">
      {header}
      {modelDraft && modelDraft.threadId === threadId && (threadId !== null || modelDraft.agentId === startingAgentId) && engines && <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Engine and model">
        <form onSubmit={(event) => { event.preventDefault(); void saveModel(); }} className="w-full max-w-md space-y-4 rounded-lg border border-zinc-200 bg-background p-5 shadow-xl dark:border-zinc-800">
          <h2 className="font-semibold">Engine and model</h2>
          <EngineModelFields engines={engines} provider={modelDraft.provider} model={modelDraft.model} onChange={(provider, model) => setModelDraft({ ...modelDraft, provider, model })} disabled={modelBusy} lockProvider={threadId !== null || startingAgentId !== null} defaultLabel={selectedAgent ? `Agent default (${modelLabel(selectedAgent.model)})` : undefined} />
          {threadId !== null && <p className="text-xs text-zinc-500">The model changes on the next message. This conversation keeps its engine.</p>}
          {modelError && <p role="alert" className="text-sm text-red-600">{modelError}</p>}
          <div className="flex gap-2"><button type="submit" disabled={modelBusy} className="rounded-md border border-zinc-300 px-3 py-2 text-sm disabled:opacity-50 dark:border-zinc-700">{modelBusy ? "Saving…" : "Use model"}</button><button type="button" disabled={modelBusy} onClick={() => setModelDraft(null)} className="rounded-md border border-zinc-300 px-3 py-2 text-sm dark:border-zinc-700">Cancel</button><button type="button" disabled={modelBusy} onClick={() => { setModelBusy(true); api.getEngines(true).then(setEngines).catch(() => setModelError("Could not refresh engines.")).finally(() => setModelBusy(false)); }} className="px-2 text-sm underline disabled:opacity-50">Refresh status</button></div>
        </form>
      </div>}
      {approvalsOpen && (
        <ApprovalsPanel
          threadId={threadId}
          workspace={thread?.workspace_path ?? null}
          onClose={() => setApprovalsOpen(false)}
        />
      )}
      {computerOpen && threadAgent && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Computer"
          onMouseDown={(event) => event.target === event.currentTarget && setComputerOpen(false)}
        >
          <div className="flex max-h-full w-full max-w-xl flex-col rounded-lg border border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
            <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
              <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">Computer · {threadAgent.name}</h2>
              <button
                onClick={() => setComputerOpen(false)}
                aria-label="Close"
                className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900"
              >
                ✕
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <ComputerView agent={threadAgent} onChanged={() => setAgentTick((n) => n + 1)} />
            </div>
          </div>
        </div>
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
            onNavigate={() => { setListOpen(false); setModelDraft(null); }}
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
                  The engine can no longer find this conversation&apos;s session, so it cannot continue it. A new
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
