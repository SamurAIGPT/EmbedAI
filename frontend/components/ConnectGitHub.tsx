"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, DeviceLogin } from "@/lib/api";
import { Button, Card, ErrorText } from "./Card";

const POLL_INTERVAL_MS = 3000;

export function ConnectGitHub({ boxId }: { boxId: string | null }) {
  const [connected, setConnected] = useState(false);
  const [checking, setChecking] = useState(true);
  const [deviceLogin, setDeviceLogin] = useState<DeviceLogin | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!boxId) {
      // No sandbox to ask about: nothing is being checked.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setChecking(false);
      return;
    }

    (async () => {
      setChecking(true);
      try {
        const status = await api.getAuthStatus(boxId, "github");
        if (!cancelled) setConnected(status.connected);
      } catch {
        // non-fatal — just leave it as "not connected"
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [boxId]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const startLogin = async () => {
    if (!boxId) return;
    setError(null);
    setStarting(true);
    try {
      const login = await api.startDeviceLogin(boxId, "github");
      setDeviceLogin(login);

      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getAuthStatus(boxId, "github");
          if (status.connected) {
            if (pollRef.current) clearInterval(pollRef.current);
            setConnected(true);
            setDeviceLogin(null);
          }
        } catch {
          // transient — keep polling
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start GitHub login.");
    } finally {
      setStarting(false);
    }
  };

  if (checking) {
    return (
      <Card step={3} title="Connect GitHub (optional)" disabled={!boxId}>
        <p className="text-sm text-muted">Checking…</p>
      </Card>
    );
  }

  if (connected) {
    return (
      <Card step={3} title="Connect GitHub (optional)" done>
        <p className="text-sm text-muted">
          GitHub is connected — tasks that push to a repo will use your account.
        </p>
      </Card>
    );
  }

  return (
    <Card step={3} title="Connect GitHub (optional)" disabled={!boxId}>
      <p className="mb-3 text-sm text-muted">
        Only needed if you want tasks to <code>git push</code> as you. Skip
        this if you don&apos;t need that.
      </p>

      {!deviceLogin && (
        <Button onClick={startLogin} disabled={starting}>
          {starting ? "Starting…" : "Connect GitHub"}
        </Button>
      )}

      {deviceLogin && (
        <div className="space-y-2">
          <p className="text-sm">
            Enter this code:{" "}
            <code className="rounded bg-inset px-2 py-1 font-mono text-base font-semibold">
              {deviceLogin.code}
            </code>
          </p>
          <a
            href={deviceLogin.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-block text-sm font-medium text-accent underline hover:opacity-80"
          >
            Open {deviceLogin.url} →
          </a>
          <p className="text-xs text-muted">
            Waiting for you to approve it — no need to come back here, this
            updates on its own.
          </p>
        </div>
      )}

      <ErrorText message={error} />
    </Card>
  );
}
