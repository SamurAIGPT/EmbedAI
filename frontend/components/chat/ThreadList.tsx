"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Thread } from "@/lib/api";

function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

const MENU_ITEM =
  "block w-full cursor-pointer px-3 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800";

type RowActions = {
  onRename: (id: number, title: string) => void;
  onArchive: (id: number, archived: boolean) => void;
  onDelete: (id: number) => void;
};

function ThreadRow({
  thread,
  selected,
  onNavigate,
  actions,
  agentName,
}: {
  thread: Thread;
  selected: boolean;
  onNavigate?: () => void;
  actions: RowActions;
  agentName?: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const archived = thread.archived_at !== null;

  // The menu closes on a click anywhere else, or on Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const away = (event: MouseEvent) => {
      if (menu.current && !menu.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => event.key === "Escape" && setMenuOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);

  const startRename = () => {
    setDraft(thread.title ?? "");
    setRenaming(true);
    setMenuOpen(false);
  };

  const saveRename = () => {
    const title = draft.trim();
    setRenaming(false);
    if (title && title !== thread.title) actions.onRename(thread.id, title);
  };

  if (confirming) {
    return (
      <li className="space-y-2 rounded-md border border-red-300 p-3 text-sm dark:border-red-900">
        <p>Delete this conversation?</p>
        <p className="text-xs text-zinc-500">
          Its messages and tool calls stored by this app are removed and cannot be restored.
          Claude&apos;s own copy of the session stays in your .claude folder.
        </p>
        <div className="flex gap-2">
          <button
            onClick={() => {
              setConfirming(false);
              actions.onDelete(thread.id);
            }}
            className="cursor-pointer rounded-md bg-red-600 px-3 py-1 text-sm font-medium text-white hover:bg-red-700"
          >
            Delete
          </button>
          <button
            onClick={() => setConfirming(false)}
            className="cursor-pointer rounded-md border border-zinc-300 px-3 py-1 text-sm dark:border-zinc-700"
          >
            Cancel
          </button>
        </div>
      </li>
    );
  }

  if (renaming) {
    return (
      <li className="px-1">
        <input
          autoFocus
          value={draft}
          maxLength={200}
          aria-label="Conversation name"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) saveRename();
            if (e.key === "Escape") setRenaming(false);
          }}
          onBlur={() => setRenaming(false)}
          className="w-full rounded-md border border-zinc-400 bg-transparent px-2 py-1.5 text-sm dark:border-zinc-600"
        />
        <p className="px-1 pt-1 text-xs text-zinc-500">Enter to save, Escape to cancel</p>
      </li>
    );
  }

  return (
    <li className="group relative">
      <Link
        href={`/chat?thread=${thread.id}`}
        onClick={onNavigate}
        aria-current={selected ? "page" : undefined}
        className={`block rounded-md py-2 pl-3 pr-9 text-sm ${
          selected ? "bg-zinc-200 dark:bg-zinc-800" : "hover:bg-zinc-100 dark:hover:bg-zinc-900"
        } ${archived ? "opacity-60" : ""}`}
      >
        <span className="block truncate">{thread.title || "New chat"}</span>
        <span className="flex items-center gap-2 text-xs text-zinc-500">
          {when(thread.updated_at)}
          {agentName && <span className="max-w-32 truncate rounded bg-zinc-200 px-1.5 dark:bg-zinc-800">{agentName}</span>}
          {archived && <span>archived</span>}
          {thread.session_state === "broken" && (
            <span className="text-amber-600 dark:text-amber-400">needs a new session</span>
          )}
        </span>
      </Link>

      <div ref={menu} className="absolute right-1 top-1.5">
        <button
          onClick={() => setMenuOpen((open) => !open)}
          aria-label="Conversation actions"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          className={`cursor-pointer rounded px-2 py-0.5 text-zinc-500 hover:bg-zinc-300 focus:opacity-100 dark:hover:bg-zinc-700 ${
            menuOpen || selected ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          }`}
        >
          ⋯
        </button>
        {menuOpen && (
          <div
            role="menu"
            className="absolute right-0 z-30 mt-1 w-40 overflow-hidden rounded-md border border-zinc-200 bg-background py-1 shadow-md dark:border-zinc-700"
          >
            <button role="menuitem" onClick={startRename} className={MENU_ITEM}>
              Rename
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                actions.onArchive(thread.id, !archived);
              }}
              className={MENU_ITEM}
            >
              {archived ? "Unarchive" : "Archive"}
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                setConfirming(true);
              }}
              className={`${MENU_ITEM} text-red-600 dark:text-red-400`}
            >
              Delete…
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

export function ThreadList({
  threads,
  selectedId,
  onNavigate,
  showArchived,
  onToggleArchived,
  actions,
  error,
  agentNames,
}: {
  // null while the first load is under way.
  threads: Thread[] | null;
  selectedId: number | null;
  // Lets a drawer close itself after a choice.
  onNavigate?: () => void;
  showArchived: boolean;
  onToggleArchived: () => void;
  actions: RowActions;
  // What went wrong with the last rename, archive or delete, if anything.
  error: string | null;
  // Agent names by id; a conversation with an agent shows its name. Missing when
  // the backend has no agents.
  agentNames?: Map<number, string>;
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
          <li className="px-3 py-2 text-xs text-zinc-500">
            {showArchived ? "No conversations." : "No conversations yet."}
          </li>
        )}
        {threads?.map((thread) => (
          <ThreadRow
            key={thread.id}
            thread={thread}
            selected={thread.id === selectedId}
            onNavigate={onNavigate}
            actions={actions}
            agentName={thread.agent_id != null ? agentNames?.get(thread.agent_id) : undefined}
          />
        ))}
      </ul>
      {error && <p className="px-4 pb-2 text-xs text-red-600 dark:text-red-400">{error}</p>}
      <div className="border-t border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <button
          onClick={onToggleArchived}
          aria-pressed={showArchived}
          className="cursor-pointer text-xs text-zinc-500 hover:underline"
        >
          {showArchived ? "Hide archived" : "Show archived"}
        </button>
      </div>
    </nav>
  );
}
