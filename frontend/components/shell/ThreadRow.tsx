"use client";

import { Archive, ArchiveRestore, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import type { Thread } from "@/lib/api";
import { cn } from "@/lib/cn";
import { t } from "@/lib/i18n";
import { rowStatus, type RowStatus } from "@/lib/sidebar";
import { Avatar } from "@/components/ui/Avatar";
import { Button } from "@/components/ui/Button";
import { Menu, MenuItem, MenuSeparator } from "@/components/ui/Menu";
import { StatusDot } from "./StatusDot";

export type RowActions = {
  onRename: (id: number, title: string) => void;
  onArchive: (id: number, archived: boolean) => void;
  onDelete: (id: number) => void;
};

function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleDateString([], { month: "short", day: "numeric" });
}

// One conversation in the Recent list: the agent's avatar, the title, when it last moved, and
// a menu to rename, archive or delete it. Rename and the delete question happen in place.
export function ThreadRow({
  thread,
  selected,
  agentName,
  status,
  actions,
  onNavigate,
}: {
  thread: Thread;
  selected: boolean;
  agentName?: string;
  // Live state, known only for the open conversation until the list carries it.
  status: { working?: boolean; waiting?: boolean };
  actions: RowActions;
  onNavigate?: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [confirming, setConfirming] = useState(false);
  // The rename field takes focus itself; the menu must not pull it back to its button.
  const keepFocus = useRef(false);
  const archived = thread.archived_at !== null;
  const shown: RowStatus = rowStatus({ ...status, brokenSession: thread.session_state === "broken" });

  const startRename = () => {
    setDraft(thread.title ?? "");
    keepFocus.current = true;
    setRenaming(true);
  };

  const saveRename = () => {
    const title = draft.trim();
    setRenaming(false);
    if (title && title !== thread.title) actions.onRename(thread.id, title);
  };

  if (confirming) {
    return (
      <li className="space-y-2 rounded-field border border-danger/50 bg-raised p-3 text-sm">
        <p className="font-medium">{t("sidebar.deleteTitle")}</p>
        <p className="text-xs text-muted">{t("sidebar.deleteBody")}</p>
        <div className="flex gap-2">
          <Button size="sm" variant="danger" onClick={() => { setConfirming(false); actions.onDelete(thread.id); }}>
            {t("common.delete")}
          </Button>
          <Button size="sm" onClick={() => setConfirming(false)}>{t("common.cancel")}</Button>
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
          aria-label={t("sidebar.renameLabel")}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) saveRename();
            if (e.key === "Escape") setRenaming(false);
          }}
          onBlur={() => setRenaming(false)}
          className="w-full rounded-field border border-line bg-raised px-2 py-1.5 text-sm"
        />
        <p className="px-1 pt-1 text-xs text-muted">{t("sidebar.renameHint")}</p>
      </li>
    );
  }

  return (
    <li className="group relative">
      <Link
        href={`/chat?thread=${thread.id}`}
        onClick={onNavigate}
        aria-current={selected ? "page" : undefined}
        className={cn(
          "flex items-center gap-2.5 rounded-field py-2 pl-2 pr-10 text-sm transition-colors duration-(--dur-fast) touch:py-2.5",
          selected ? "bg-hover" : "hover:bg-hover/60",
          archived && "opacity-60",
        )}
      >
        <Avatar agentId={thread.agent_id ?? null} name={agentName} size="md" />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="min-w-0 flex-1 truncate font-medium">{thread.title || t("sidebar.untitled")}</span>
            <StatusDot status={shown === "reset" ? null : shown} />
          </span>
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span className="shrink-0">{when(thread.updated_at)}</span>
            {agentName ? <span className="min-w-0 truncate">· {agentName}</span> : null}
            {archived ? <span>· {t("sidebar.archivedTag")}</span> : null}
            {shown === "reset" ? <span className="text-warning">· {t("status.reset")}</span> : null}
          </span>
        </span>
      </Link>

      <div className={cn("absolute right-1 top-1/2 -translate-y-1/2", !selected && "opacity-0 focus-within:opacity-100 group-hover:opacity-100 touch:opacity-100")}>
        <Menu
          trigger={
            <button
              type="button"
              aria-label={t("sidebar.rowActions")}
              className="inline-flex size-8 cursor-pointer items-center justify-center rounded-field text-muted hover:bg-hover hover:text-ink touch:size-10"
            >
              <MoreHorizontal size={16} aria-hidden="true" />
            </button>
          }
          onCloseAutoFocus={(event) => {
            if (keepFocus.current) {
              event.preventDefault();
              keepFocus.current = false;
            }
          }}
        >
          <MenuItem onSelect={startRename} icon={<Pencil size={14} />}>{t("sidebar.rename")}</MenuItem>
          <MenuItem
            onSelect={() => actions.onArchive(thread.id, !archived)}
            icon={archived ? <ArchiveRestore size={14} /> : <Archive size={14} />}
          >
            {archived ? t("sidebar.unarchive") : t("sidebar.archive")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem onSelect={() => setConfirming(true)} danger icon={<Trash2 size={14} />}>{t("sidebar.deleteAsk")}</MenuItem>
        </Menu>
      </div>
    </li>
  );
}
