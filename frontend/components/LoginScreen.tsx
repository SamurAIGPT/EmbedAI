"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError } from "@/lib/api";
import { auth } from "@/lib/auth";
import { safeNextPath, type AuthStatus } from "@/lib/session";

const inputClass = "block w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm";
const buttonClass = "w-full cursor-pointer rounded-md px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 bg-accent text-accent-fg hover:opacity-90";

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
    <div className="flex min-h-dvh items-center justify-center bg-inset px-4">
      <main className="w-full max-w-sm space-y-5 rounded-lg border border-line bg-raised p-6">
        <h1 className="text-lg font-semibold">{settingUp ? "Set the owner passphrase" : "Sign in to Open Duet"}</h1>
        {problem && <p role="alert" className="text-sm text-danger">{problem}</p>}
        {!status && !problem && <p className="text-sm text-muted">Loading…</p>}
        {settingUp && !status.setup_allowed && (
          <div className="space-y-2 text-sm text-muted">
            <p>No owner passphrase is set yet, and it can only be set from the server itself. On the server, run:</p>
            <pre className="overflow-x-auto rounded bg-inset p-3 text-xs">cd backend{"\n"}uv run python -m app.auth.cli set-passphrase</pre>
            <p>Then reload this page and sign in.</p>
          </div>
        )}
        {status && (!settingUp || status.setup_allowed) && (
          <form onSubmit={submit} className="space-y-4">
            {settingUp && <p className="text-sm text-muted">This passphrase protects this server. Use at least 10 characters; you will sign in with it on every new browser.</p>}
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
            {error && <p role="alert" className="text-sm text-danger">{error}</p>}
            <button type="submit" className={buttonClass} disabled={busy}>{busy ? "Working…" : settingUp ? "Set passphrase and sign in" : "Sign in"}</button>
          </form>
        )}
        {status && !settingUp && (
          <p className="text-xs text-muted">Have a pairing code from Settings → Devices? <Link href="/pair/" className="underline">Pair this browser</Link>.</p>
        )}
      </main>
    </div>
  );
}
