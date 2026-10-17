"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/everyday";
import type { AttachmentRef } from "@/lib/transcript";

// An attached image, fetched with the session and shown from an object URL.
function Thumbnail({ threadId, item }: { threadId: number; item: AttachmentRef }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    let made: string | null = null;
    api
      .attachmentBlobUrl(threadId, item.id)
      .then((value) => {
        made = value;
        if (alive) setUrl(value);
        else URL.revokeObjectURL(value);
      })
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [threadId, item.id]);
  if (failed) return <FileChip item={item} note="image unavailable" />;
  if (!url) return <span className="inline-block h-24 w-24 animate-pulse rounded-lg bg-zinc-200 dark:bg-zinc-800" />;
  return (
    <a href={url} target="_blank" rel="noreferrer" title={item.name}>
      {/* eslint-disable-next-line @next/next/no-img-element -- a local object URL, not an optimisable asset */}
      <img src={url} alt={item.name} className="max-h-40 max-w-56 rounded-lg border border-zinc-200 object-cover dark:border-zinc-700" />
    </a>
  );
}

function FileChip({ item, note }: { item: { name: string; bytes: number }; note?: string }) {
  return (
    <span className="inline-flex max-w-56 items-center gap-1 rounded-md border border-zinc-300 bg-background px-2 py-1 text-xs text-zinc-700 dark:border-zinc-700 dark:text-zinc-300">
      <span aria-hidden>📄</span>
      <span className="truncate">{item.name}</span>
      <span className="shrink-0 text-zinc-500">{note ?? formatBytes(item.bytes)}</span>
    </span>
  );
}

// What was attached to a sent message: images as thumbnails, other files as chips.
export function MessageAttachments({ threadId, items }: { threadId: number | null; items: AttachmentRef[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap justify-end gap-2">
      {items.map((item) =>
        item.kind === "image" && threadId !== null ? (
          <Thumbnail key={item.id} threadId={threadId} item={item} />
        ) : (
          <FileChip key={item.id} item={item} />
        )
      )}
    </div>
  );
}

// Files chosen for the next message, before they are sent.
export function PendingAttachments({
  files,
  onRemove,
  disabled,
}: {
  files: File[];
  onRemove: (index: number) => void;
  disabled: boolean;
}) {
  const [previews, setPreviews] = useState<Map<File, string>>(new Map());
  useEffect(() => {
    let alive = true;
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      const reader = new FileReader();
      reader.onload = () => {
        if (alive && typeof reader.result === "string") {
          const url = reader.result;
          setPreviews((previous) => new Map(previous).set(file, url));
        }
      };
      reader.readAsDataURL(file);
    }
    return () => {
      alive = false;
    };
  }, [files]);
  if (files.length === 0) return null;
  return (
    <ul aria-label="Attached files" className="mb-2 flex flex-wrap gap-2">
      {files.map((file, index) => (
        <li
          key={`${file.name}-${index}`}
          className="flex items-center gap-2 rounded-md border border-zinc-300 px-2 py-1 text-xs dark:border-zinc-700"
        >
          {previews.get(file) ? (
            // eslint-disable-next-line @next/next/no-img-element -- a local data URL
            <img src={previews.get(file)} alt="" className="h-8 w-8 rounded object-cover" />
          ) : (
            <span aria-hidden>📄</span>
          )}
          <span className="max-w-40 truncate">{file.name}</span>
          <span className="text-zinc-500">{formatBytes(file.size)}</span>
          <button
            type="button"
            onClick={() => onRemove(index)}
            disabled={disabled}
            aria-label={`Remove ${file.name}`}
            className="cursor-pointer rounded px-1 text-zinc-500 hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800"
          >
            ✕
          </button>
        </li>
      ))}
    </ul>
  );
}
