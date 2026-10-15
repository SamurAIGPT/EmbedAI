"use client";

import Link from "next/link";
import type { Thread } from "@/lib/api";

function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

export function ThreadList({
  threads,
  selectedId,
  onNavigate,
}: {
  // null while the first load is under way.
  threads: Thread[] | null;
  selectedId: number | null;
  // Lets a drawer close itself after a choice.
  onNavigate?: () => void;
}) {
  return (
    <nav aria-label="Conversations" className="flex min-h-0 flex-1 flex-col">
      <div className="p-3">
        <Link
          href="/chat"
          onClick={onNavigate}
          className="block rounded-md border border-zinc-300 px-3 py-2 text-center text-sm font-medium hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
        >
          New chat
        </Link>
      </div>
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {threads === null && <li className="px-3 py-2 text-xs text-zinc-500">Loading…</li>}
        {threads?.length === 0 && (
          <li className="px-3 py-2 text-xs text-zinc-500">No conversations yet.</li>
        )}
        {threads?.map((thread) => {
          const selected = thread.id === selectedId;
          return (
            <li key={thread.id}>
              <Link
                href={`/chat?thread=${thread.id}`}
                onClick={onNavigate}
                aria-current={selected ? "page" : undefined}
                className={`block rounded-md px-3 py-2 text-sm ${
                  selected
                    ? "bg-zinc-200 dark:bg-zinc-800"
                    : "hover:bg-zinc-100 dark:hover:bg-zinc-900"
                }`}
              >
                <span className="block truncate">{thread.title || "New chat"}</span>
                <span className="flex items-center gap-2 text-xs text-zinc-500">
                  {when(thread.updated_at)}
                  {thread.session_state === "broken" && (
                    <span className="text-amber-600 dark:text-amber-400">needs a new session</span>
                  )}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
