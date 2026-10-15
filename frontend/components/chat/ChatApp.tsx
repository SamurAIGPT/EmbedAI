"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError, type LocalStatus, type Thread } from "@/lib/api";
import { pendingApprovals } from "@/lib/transcript";
import { Composer } from "./Composer";
import { Conversation } from "./Conversation";
import { ThreadList } from "./ThreadList";
import { useThreadStream } from "./useThreadStream";

const LIST_REFRESH_MS = 15_000;

type Gate =
  | { phase: "loading" }
  | { phase: "unreachable" }
  | { phase: "ready"; status: LocalStatus };

function parseThreadId(raw: string | null): number | null {
  return raw !== null && /^\d+$/.test(raw) ? Number(raw) : null;
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

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-1 items-center justify-center p-6">
      <section className="max-w-md rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
        <h2 className="text-base font-semibold">{title}</h2>
        <div className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{children}</div>
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

  const stream = useThreadStream(gate.phase === "ready" && gate.status.available ? threadId : null);
  const { transcript } = stream;

  useEffect(() => {
    let cancelled = false;
    api
      .getLocalStatus()
      .then((status) => !cancelled && setGate({ phase: "ready", status }))
      .catch((err: unknown) => {
        if (cancelled) return;
        // An answer that is an error (an older backend without the route) is not "unreachable".
        setGate(err instanceof ApiError ? { phase: "ready", status: { enabled: false, available: false, reason: null, cli_version: null } } : { phase: "unreachable" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const ready = gate.phase === "ready" && gate.status.available;

  // The list is read whenever the tick changes, and again when a turn starts or
  // ends (that changes the order, the title and the session state).
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    api
      .listThreads()
      .then((list) => !cancelled && setThreads(list))
      .catch(() => {
        // keep what is shown; the next refresh tries again
      });
    return () => {
      cancelled = true;
    };
  }, [ready, listTick, transcript.runningTurnId]);

  useEffect(() => {
    if (!ready) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") setListTick((n) => n + 1);
    }, LIST_REFRESH_MS);
    return () => clearInterval(timer);
  }, [ready]);

  const thread = threads?.find((t) => t.id === threadId) ?? null;
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
        id = (await api.createThread()).id;
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

  const answer = async (approvalId: string, decision: "allow" | "deny") => {
    if (threadId === null) return;
    setAnswering(approvalId);
    setApprovalErrors((prev) => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== approvalId)));
    try {
      await api.answerThreadApproval(threadId, approvalId, decision);
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
      <Link href="/" className="text-sm text-zinc-500 hover:underline">
        ← Home
      </Link>
      <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">
        {thread?.title || (threadId === null ? "New chat" : "Chat")}
      </h1>
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
        <Notice title={gate.status.enabled ? "Local Claude is not available" : "Local Claude is turned off"}>
          {gate.status.enabled
            ? (gate.status.reason ?? "unknown reason")
            : "Set LOCAL_EXECUTION_ENABLED=true in backend/.env on the machine running the backend, then restart it. Chat runs Claude through your own signed-in CLI on this computer."}
        </Notice>
      </div>
    );
  }

  return (
    <div className="relative flex h-dvh flex-col">
      {header}
      <div className="relative flex min-h-0 flex-1">
        <aside
          className={`${
            listOpen ? "flex" : "hidden"
          } absolute inset-y-0 left-0 z-20 w-72 shrink-0 flex-col border-r border-zinc-200 bg-background md:static md:flex dark:border-zinc-800`}
        >
          <ThreadList threads={threads} selectedId={threadId} onNavigate={() => setListOpen(false)} />
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
          />
        </main>
      </div>
    </div>
  );
}
