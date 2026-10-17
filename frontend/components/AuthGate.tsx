"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { isSignedOutError, setSignedOutHandler } from "@/lib/api";
import { auth } from "@/lib/auth";
import { migrateLegacyIdentity, type SessionInfo } from "@/lib/session";

type SessionContextValue = {
  // null when the server could not say (unreachable, or the desktop app's page
  // opened outside its window): pages then show their own errors.
  session: SessionInfo | null;
  setUserId: (userId: string | null) => void;
};

const SessionContext = createContext<SessionContextValue>({ session: null, setUserId: () => {} });

export function useSession() {
  return useContext(SessionContext);
}

// Renders its children once the browser is known to be signed in (or is the
// desktop app, or trusted on this machine); sends it to the sign-in page otherwise.
export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    setSignedOutHandler(() => {
      const next = `${window.location.pathname}${window.location.search}`;
      router.replace(`/login/?next=${encodeURIComponent(next)}`);
    });
    (async () => {
      try {
        const current = await auth.session();
        let storage: Storage | null = null;
        try { storage = window.localStorage; } catch { /* storage blocked */ }
        const userId = storage ? await migrateLegacyIdentity(current, storage, auth.setIdentity) : current.user_id;
        if (!cancelled) setSession({ ...current, user_id: userId });
      } catch (err) {
        if (isSignedOutError(err)) return; // the handler is already navigating
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
      setSignedOutHandler(null);
    };
  }, [router]);

  const setUserId = useCallback((userId: string | null) => {
    setSession((previous) => (previous ? { ...previous, user_id: userId } : previous));
  }, []);

  if (!ready) return <p className="p-6 text-center text-sm text-zinc-500">Loading…</p>;
  return <SessionContext.Provider value={{ session, setUserId }}>{children}</SessionContext.Provider>;
}
