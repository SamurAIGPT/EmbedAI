"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { auth } from "@/lib/auth";
import { safeNextPath, type AuthStatus } from "@/lib/session";

const inputClass = "block w-full rounded-md border border-zinc-300 bg-transparent px-3 py-2 text-sm dark:border-zinc-700";
const buttonClass = "w-full cursor-pointer rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300";

function nextPath(): string {
  return safeNextPath(new URLSearchParams(window.location.search).get("next"));
}

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

// Sign-in, or first-run setup of the owner passphrase (docs/AUTH.md).
export function LoginScreen() {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    auth.status().then((value) => {
      if (cancelled) return;
      // Already signed in, or the desktop app (which never signs in): go on.
      if (value.signed_in || value.desktop) window.location.replace(nextPath());
      else setStatus(value);
    }).catch((err: unknown) => {
      if (!cancelled) setProblem(message(err, `Cannot reach the backend from ${window.location.origin}. Check that it is running and that this address is in FRONTEND_ORIGINS.`));
    });
    return () => { cancelled = true; };
  }, []);

  const settingUp = status !== null && !status.owner_configured;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (settingUp && passphrase !== repeat) {
      setError("The two passphrases differ.");
      return;
    }
    setBusy(true);
    try {
      if (settingUp) await auth.setup(passphrase);
      else await auth.login(passphrase);
      window.location.replace(nextPath());
    } catch (err) {
      setError(message(err, "Could not sign in. Try again."));
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-zinc-50 px-4 dark:bg-black">
      <main className="w-full max-w-sm space-y-5 rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-950">
        <h1 className="text-lg font-semibold">{settingUp ? "Set the owner passphrase" : "Sign in to Open Dots"}</h1>
        {problem && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{problem}</p>}
        {!status && !problem && <p className="text-sm text-zinc-500">Loading…</p>}
        {settingUp && !status.setup_allowed && (
          <div className="space-y-2 text-sm text-zinc-600 dark:text-zinc-400">
            <p>No owner passphrase is set yet, and it can only be set from the server itself. On the server, run:</p>
            <pre className="overflow-x-auto rounded bg-zinc-100 p-3 text-xs dark:bg-zinc-900">cd backend{"\n"}uv run python -m app.auth.cli set-passphrase</pre>
            <p>Then reload this page and sign in.</p>
          </div>
        )}
        {status && (!settingUp || status.setup_allowed) && (
          <form onSubmit={submit} className="space-y-4">
            {settingUp && <p className="text-sm text-zinc-600 dark:text-zinc-400">This passphrase protects this server. Use at least 10 characters; you will sign in with it on every new browser.</p>}
            <div className="space-y-1">
              <label htmlFor="passphrase" className="block text-sm font-medium">Passphrase</label>
              <input id="passphrase" type="password" className={inputClass} value={passphrase} onChange={(e) => setPassphrase(e.target.value)}
                autoComplete={settingUp ? "new-password" : "current-password"} required minLength={settingUp ? 10 : 1} autoFocus />
            </div>
            {settingUp && (
              <div className="space-y-1">
                <label htmlFor="repeat" className="block text-sm font-medium">Repeat it</label>
                <input id="repeat" type="password" className={inputClass} value={repeat} onChange={(e) => setRepeat(e.target.value)} autoComplete="new-password" required />
              </div>
            )}
            {error && <p role="alert" className="text-sm text-red-600 dark:text-red-400">{error}</p>}
            <button type="submit" className={buttonClass} disabled={busy}>{busy ? "Working…" : settingUp ? "Set passphrase and sign in" : "Sign in"}</button>
          </form>
        )}
        {status && !settingUp && (
          <p className="text-xs text-zinc-500">Have a pairing code from Settings → Devices? <Link href="/pair/" className="underline">Pair this browser</Link>.</p>
        )}
      </main>
    </div>
  );
}
