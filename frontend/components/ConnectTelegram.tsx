"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, LinkCode } from "@/lib/api";
import { Button, Card, ErrorText } from "./Card";

const POLL_INTERVAL_MS = 2000;

function isGuestId(userId: string) {
  return userId.startsWith("web_");
}

export function ConnectTelegram({
  userId,
  onLinked,
  onReset,
}: {
  userId: string | null;
  // The server sets the identity: from the link code it saw claimed, or a new guest id.
  onLinked: (identity: { link_code: string } | { guest: true }) => void;
  onReset: () => void;
}) {
  const [mode, setMode] = useState<"choice" | "telegram">("choice");
  const [linkCode, setLinkCode] = useState<LinkCode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const generateCode = async () => {
    setError(null);
    setLoading(true);
    try {
      const code = await api.createLinkCode();
      setLinkCode(code);

      pollRef.current = setInterval(async () => {
        try {
          const status = await api.getLinkStatus(code.code);
          if (status.status === "claimed" && status.user_id) {
            if (pollRef.current) clearInterval(pollRef.current);
            setLinkCode(null);
            onLinked({ link_code: code.code });
          } else if (status.status === "expired") {
            if (pollRef.current) clearInterval(pollRef.current);
            setError("Code expired — generate a new one.");
            setLinkCode(null);
          }
        } catch {
          // transient network hiccup — keep polling
        }
      }, POLL_INTERVAL_MS);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not generate a code.");
    } finally {
      setLoading(false);
    }
  };

  if (userId) {
    return (
      <Card step={1} title="Get started" done>
        {isGuestId(userId) ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Continuing without Telegram.
          </p>
        ) : (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Linked as Telegram user <code className="font-mono">{userId}</code>.
          </p>
        )}
        <button
          onClick={onReset}
          className="mt-2 text-sm text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-200"
        >
          Start over
        </button>
      </Card>
    );
  }

  const botLink = linkCode?.bot_username
    ? `https://t.me/${linkCode.bot_username}`
    : null;

  if (mode === "choice") {
    return (
      <Card step={1} title="Get started">
        <p className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">
          You don&apos;t need Telegram to use this — continue as a guest, or
          link Telegram if you want the same account/history there too.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => onLinked({ guest: true })}>
            Continue without Telegram
          </Button>
          <button
            onClick={() => setMode("telegram")}
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            Link Telegram instead
          </button>
        </div>
      </Card>
    );
  }

  return (
    <Card step={1} title="Get started">
      <p className="mb-3 text-sm text-zinc-600 dark:text-zinc-400">
        Generates a one-time code. Send it to the bot in Telegram to link your
        account — no token or password needed here.
      </p>

      {!linkCode && (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={generateCode} disabled={loading}>
            {loading ? "Generating…" : "Generate code"}
          </Button>
          <button
            onClick={() => setMode("choice")}
            className="text-sm text-zinc-500 underline hover:text-zinc-800 dark:hover:text-zinc-200"
          >
            Back
          </button>
        </div>
      )}

      {linkCode && (
        <div className="space-y-2">
          <p className="text-sm">
            Send this to the bot:{" "}
            <code className="rounded bg-zinc-100 px-2 py-1 font-mono text-base font-semibold dark:bg-zinc-800">
              /link {linkCode.code}
            </code>
          </p>
          {botLink && (
            <a
              href={botLink}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block text-sm font-medium text-blue-600 underline hover:text-blue-800 dark:text-blue-400"
            >
              Open Telegram →
            </a>
          )}
          <p className="text-xs text-zinc-500">Waiting for you to send it…</p>
        </div>
      )}

      <ErrorText message={error} />
    </Card>
  );
}
