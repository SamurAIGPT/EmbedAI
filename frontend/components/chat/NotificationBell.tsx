"use client";

import { Bell } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type AppNotification } from "@/lib/api";
import { badgeText, formatWhen } from "@/lib/routines";
import { t } from "@/lib/i18n";
import { IconButton } from "@/components/ui/IconButton";

const POLL_MS = 15_000;

// The header's bell: the unread count, and the feed when opened. A click on an
// entry marks it read and opens its conversation when it has one.
export function NotificationBell({
  elevated = false,
  onOpen,
}: {
  elevated?: boolean;
  onOpen?: () => void;
}) {
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
    <div className={`relative ${elevated ? "z-50" : ""}`}>
      <IconButton
        label={badge ? t("notifications.unread", { count: unread }) : t("notifications.title")}
        aria-expanded={open}
        onClick={() => {
          if (!open) onOpen?.();
          setOpen(!open);
        }}
        className="relative"
      >
        <Bell size={18} />
        {badge && (
          <span className="absolute right-0.5 top-0.5 min-w-4 rounded-full bg-danger px-1 text-center text-[10px] font-semibold leading-4 text-accent-fg">{badge}</span>
        )}
      </IconButton>
      {open && (
        <div role="dialog" aria-label={t("notifications.title")} className="absolute right-0 top-11 z-40 w-[min(20rem,calc(100vw-1.5rem))] animate-pop-in rounded-card border border-line bg-raised shadow-xl">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <p className="flex-1 text-xs font-semibold">{t("notifications.title")}</p>
            <button onClick={() => void readAll()} className="cursor-pointer text-xs text-accent underline">{t("notifications.markAll")}</button>
          </div>
          <ul className="max-h-96 overflow-y-auto">
            {items === null ? (
              <li className="p-3 text-xs text-muted">{t("common.loading")}</li>
            ) : items.length === 0 ? (
              <li className="p-3 text-xs text-muted">{t("notifications.empty")}</li>
            ) : (
              items.map((item) => (
                <li key={item.id}>
                  <button onClick={() => void choose(item)} className={`block w-full cursor-pointer px-3 py-2 text-left hover:bg-hover ${item.read_at ? "" : "bg-accent/10"}`}>
                    <p className="text-xs font-medium">{item.title}</p>
                    {item.body && <p className="line-clamp-2 text-xs text-muted">{item.body}</p>}
                    <p className="text-[10px] text-faint">{formatWhen(item.created_at)}</p>
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
