"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Button, Card, ErrorText } from "./Card";

export function ConnectClaude({
  userId,
  onReady,
  onSandbox,
}: {
  userId: string;
  // Claude is signed in inside the sandbox: tasks can be submitted.
  onReady: (boxId: string) => void;
  // The sandbox exists (Claude may not be signed in yet): enough for GitHub login.
  onSandbox?: (boxId: string) => void;
}) {
  const [boxId, setBoxId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [loginUrl, setLoginUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [status, setStatus] = useState<
    "loading" | "idle" | "starting" | "submitting"
  >("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setStatus("loading");
      setError(null);
      try {
        const sandbox = await api.ensureSandbox(userId);
        if (cancelled) return;
        setBoxId(sandbox.box_id);
        onSandbox?.(sandbox.box_id);

        const authStatus = await api.getClaudeStatus(sandbox.box_id);
        if (cancelled) return;
        setConnected(authStatus.connected);
        if (authStatus.connected) onReady(sandbox.box_id);
        setStatus("idle");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Could not set up sandbox.");
        setStatus("idle");
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId]);

  const startLogin = async () => {
    if (!boxId) return;
    setError(null);
    setStatus("starting");
    try {
      const { login_url } = await api.startClaudeLogin(boxId);
      setLoginUrl(login_url);
      setStatus("idle");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start login.");
      setStatus("idle");
    }
  };

  const submitCode = async () => {
    if (!boxId || !code.trim()) return;
    setError(null);
    setStatus("submitting");
    try {
      await api.submitClaudeCode(boxId, code.trim());
      setConnected(true);
      setLoginUrl(null);
      onReady(boxId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed.");
    } finally {
      setStatus("idle");
    }
  };

  if (status === "loading") {
    return (
      <Card step={2} title="Connect Claude">
        <p className="text-sm text-muted">Setting up your sandbox…</p>
      </Card>
    );
  }

  if (connected) {
    return (
      <Card step={2} title="Connect Claude" done>
        <p className="text-sm text-muted">
          Claude is connected for this account.
        </p>
      </Card>
    );
  }

  return (
    <Card step={2} title="Connect Claude">
      {!loginUrl && (
        <Button onClick={startLogin} disabled={status === "starting" || !boxId}>
          {status === "starting" ? "Starting…" : "Start login"}
        </Button>
      )}

      {loginUrl && (
        <div className="space-y-3">
          <a
            href={loginUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="block text-sm font-medium text-accent underline hover:opacity-80"
          >
            Open Claude login →
          </a>
          <p className="text-sm text-muted">
            After logging in, paste the code it gives you:
          </p>
          <div className="flex gap-2">
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="paste code here"
              className="w-full max-w-xs rounded-md border border-line px-3 py-2 text-sm"
            />
            <Button onClick={submitCode} disabled={status === "submitting" || !code.trim()}>
              {status === "submitting" ? "Submitting…" : "Submit"}
            </Button>
          </div>
        </div>
      )}

      <ErrorText message={error} />
    </Card>
  );
}
