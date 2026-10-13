"use client";

import { useEffect, useState } from "react";
import { ConnectTelegram } from "@/components/ConnectTelegram";
import { ConnectClaude } from "@/components/ConnectClaude";
import { ConnectGitHub } from "@/components/ConnectGitHub";
import { SubmitTask } from "@/components/SubmitTask";
import { LocalClaude } from "@/components/LocalClaude";

const STORAGE_KEY = "vadoo_user_id";

export default function Home() {
  const [userId, setUserId] = useState<string | null>(null);
  const [boxId, setBoxId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setUserId(localStorage.getItem(STORAGE_KEY));
    setLoaded(true);
  }, []);

  const handleLinked = (linkedUserId: string) => {
    localStorage.setItem(STORAGE_KEY, linkedUserId);
    setUserId(linkedUserId);
  };

  const handleReset = () => {
    localStorage.removeItem(STORAGE_KEY);
    setUserId(null);
    setBoxId(null);
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

        {!loaded ? (
          <p className="text-center text-sm text-zinc-500">Loading…</p>
        ) : (
          <>
            <LocalClaude />

            <ConnectTelegram userId={userId} onLinked={handleLinked} onReset={handleReset} />

            {userId ? (
              <ConnectClaude key={userId} userId={userId} onReady={setBoxId} />
            ) : (
              <section className="rounded-lg border border-zinc-200 p-5 opacity-50 dark:border-zinc-800">
                <h2 className="text-base font-semibold">2. Connect Claude</h2>
              </section>
            )}

            {userId ? (
              <ConnectGitHub boxId={boxId} />
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
        )}
      </main>
    </div>
  );
}
