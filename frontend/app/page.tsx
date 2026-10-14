"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { ConnectTelegram } from "@/components/ConnectTelegram";
import { ConnectClaude } from "@/components/ConnectClaude";
import { ConnectGitHub } from "@/components/ConnectGitHub";
import { SubmitTask } from "@/components/SubmitTask";
import { LocalClaude } from "@/components/LocalClaude";

const STORAGE_KEY = "vadoo_user_id";

export default function Home() {
  const [userId, setUserId] = useState<string | null>(null);
  // Claude is signed in inside the sandbox: tasks can be submitted.
  const [boxId, setBoxId] = useState<string | null>(null);
  // The sandbox exists, signed in or not: enough to connect GitHub.
  const [sandboxId, setSandboxId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // null until the backend has said whether Boat sandboxes are configured.
  const [boatEnabled, setBoatEnabled] = useState<boolean | null>(null);
  const [boatReason, setBoatReason] = useState<string | null>(null);
  // Set when the backend could not be reached at all (not when it answered with an error).
  const [backendProblem, setBackendProblem] = useState<string | null>(null);

  useEffect(() => {
    setUserId(localStorage.getItem(STORAGE_KEY));
    setLoaded(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .getProviders()
      .then((providers) => {
        if (cancelled) return;
        setBoatEnabled(providers.boat.enabled);
        setBoatReason(providers.boat.reason);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError) {
          // The backend answered but has no /providers (an older version):
          // behave as before and show the Boat steps.
          setBoatEnabled(true);
          return;
        }
        // No answer at all: the backend is down, or the browser blocked the
        // request because this page's address is not in FRONTEND_ORIGINS.
        setBackendProblem(
          `Cannot reach the backend. Check that it is running, and that this page's address (${window.location.origin}) is listed in FRONTEND_ORIGINS in backend/.env.`
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLinked = (linkedUserId: string) => {
    localStorage.setItem(STORAGE_KEY, linkedUserId);
    setUserId(linkedUserId);
  };

  const handleReset = () => {
    localStorage.removeItem(STORAGE_KEY);
    setUserId(null);
    setBoxId(null);
    setSandboxId(null);
  };

  return (
    <div className="flex min-h-screen flex-col items-center bg-zinc-50 px-4 py-12 dark:bg-black">
      <main className="w-full max-w-lg space-y-4">
        <div className="mb-6 text-center">
          <h1 className="text-xl font-semibold">Vadoo Autonomous Agent</h1>
          <p className="text-sm text-zinc-500">
            Connect your accounts, then submit a task.
          </p>
        </div>

        {backendProblem ? (
          <section className="rounded-lg border border-red-300 p-5 dark:border-red-800">
            <h2 className="text-base font-semibold">Backend not reachable</h2>
            <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{backendProblem}</p>
          </section>
        ) : !loaded || boatEnabled === null ? (
          <p className="text-center text-sm text-zinc-500">Loading…</p>
        ) : (
          <>
            <LocalClaude />

            <ConnectTelegram userId={userId} onLinked={handleLinked} onReset={handleReset} />

            {boatEnabled ? (
              <>
                {userId ? (
                  <ConnectClaude
                    key={userId}
                    userId={userId}
                    onReady={setBoxId}
                    onSandbox={setSandboxId}
                  />
                ) : (
                  <section className="rounded-lg border border-zinc-200 p-5 opacity-50 dark:border-zinc-800">
                    <h2 className="text-base font-semibold">2. Connect Claude</h2>
                  </section>
                )}

                {userId ? (
                  <ConnectGitHub boxId={sandboxId} />
                ) : (
                  <section className="rounded-lg border border-zinc-200 p-5 opacity-50 dark:border-zinc-800">
                    <h2 className="text-base font-semibold">3. Connect GitHub (optional)</h2>
                  </section>
                )}

                {userId ? (
                  <SubmitTask userId={userId} boxId={boxId} />
                ) : (
                  <section className="rounded-lg border border-zinc-200 p-5 opacity-50 dark:border-zinc-800">
                    <h2 className="text-base font-semibold">4. Submit a task</h2>
                  </section>
                )}
              </>
            ) : (
              <>
                <section className="rounded-lg border border-zinc-200 p-5 dark:border-zinc-800">
                  <h2 className="text-base font-semibold">Boat sandboxes are off</h2>
                  <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
                    Connecting Claude in a sandbox, GitHub, schedules and tool tasks need
                    Boat. Local Claude above works without it.
                    {boatReason ? ` (${boatReason}.)` : ""}
                  </p>
                </section>

                {userId && (
                  <SubmitTask
                    userId={userId}
                    boxId={null}
                    step={2}
                    showComposer={false}
                    blockedReason="New Boat tasks are unavailable while Boat is off."
                  />
                )}
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
}
