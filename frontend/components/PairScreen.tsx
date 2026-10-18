"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { auth } from "@/lib/auth";
import { pairingCodeFromHash } from "@/lib/session";

const inputClass = "block w-full rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm dark:border-zinc-700";
const buttonClass = "w-full cursor-pointer rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";

// Exchanges a one-time pairing code for this browser's own session. The code
// arrives in the URL fragment (#code=…), which never reaches a server log.
export function PairScreen() {
  const [code, setCode] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const fromLink = pairingCodeFromHash(window.location.hash);
    if (fromLink) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCode(fromLink);
      // Out of the address bar and history once read.
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await auth.pair(code, label);
      window.location.replace("/chat/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not pair. Check the code and try again.");
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-zinc-50 px-4 dark:bg-black">
      <main className="w-full max-w-sm space-y-5 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950">
        <h1 className="text-lg font-semibold">Pair this browser</h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">Enter the code shown in Settings → Devices on a signed-in browser. Codes work once and expire after five minutes.</p>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1">
            <label htmlFor="code" className="block text-sm font-medium">Pairing code</label>
            <input id="code" className={`${inputClass} font-mono uppercase tracking-widest`} value={code} onChange={(e) => setCode(e.target.value)}
              placeholder="ABCD-EFGH" autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false} required maxLength={32} />
          </div>
          <div className="space-y-1">
            <label htmlFor="label" className="block text-sm font-medium">Name for this device (optional)</label>
            <input id="label" className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="My phone" maxLength={80} />
          </div>
          {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
          <button type="submit" className={buttonClass} disabled={busy || !code.trim()}>{busy ? "Pairing…" : "Pair"}</button>
        </form>
        <p className="text-xs text-zinc-500">Are you the owner? <Link href="/login/" className="underline">Sign in with the passphrase</Link>.</p>
      </main>
    </div>
  );
}
