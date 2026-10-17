"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type AppNotification } from "@/lib/api";
import { badgeText, formatWhen } from "@/lib/routines";

const POLL_MS = 15_000;

// The header's bell: the unread count, and the feed when opened. A click on an
// entry marks it read and opens its conversation when it has one.
export function NotificationBell() {
  const router = useRouter();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const [failed, setFailed] = useState(false);

  const refresh = useCallback(() => {
    api.unreadNotifications().then((r) => { setUnread(r.unread); setFailed(false); }).catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    api.listNotifications().then(setItems).catch(() => setItems([]));
  }, [open, unread]);

  const choose = async (item: AppNotification) => {
    if (!item.read_at) await api.readNotification(item.id).catch(() => undefined);
    setOpen(false);
    refresh();
    if (item.thread_id !== null) router.push(`/chat?thread=${item.thread_id}`);
  };

  const readAll = async () => {
    await api.readAllNotifications().catch(() => undefined);
    setItems((current) => current?.map((item) => ({ ...item, read_at: item.read_at ?? new Date().toISOString() })) ?? null);
    refresh();
  };

  if (failed && unread === 0 && !open) return null; // an older backend without the feed
  const badge = badgeText(unread);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((on) => !on)}
        aria-label={badge ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        className="relative cursor-pointer rounded-md border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-900"
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {badge && (
          <span className="absolute -right-2 -top-2 min-w-5 rounded-full bg-red-600 px-1 text-center text-[10px] font-semibold leading-5 text-white">{badge}</span>
        )}
      </button>
      {open && (
        <div role="dialog" aria-label="Notifications" className="absolute right-0 top-9 z-40 w-80 rounded-lg border border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
          <div className="flex items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
            <p className="flex-1 text-xs font-semibold">Notifications</p>
            <button onClick={() => void readAll()} className="cursor-pointer text-xs underline">Mark all read</button>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {items === null ? (
              <li className="p-3 text-xs text-zinc-500">Loading…</li>
            ) : items.length === 0 ? (
              <li className="p-3 text-xs text-zinc-500">Nothing yet.</li>
            ) : (
              items.map((item) => (
                <li key={item.id}>
                  <button onClick={() => void choose(item)} className={`block w-full cursor-pointer px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-900 ${item.read_at ? "" : "bg-sky-50 dark:bg-sky-950/30"}`}>
                    <p className="text-xs font-medium">{item.title}</p>
                    {item.body && <p className="line-clamp-2 text-xs text-zinc-600 dark:text-zinc-400">{item.body}</p>}
                    <p className="text-[10px] text-zinc-500">{formatWhen(item.created_at)}</p>
                  </button>
                </li>
              ))
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
