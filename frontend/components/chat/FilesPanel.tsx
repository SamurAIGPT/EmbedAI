"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type FilePreview, type FolderListing } from "@/lib/api";
import { formatBytes, parentPath } from "@/lib/everyday";

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message || fallback : "Could not reach the backend.";
}

// Read-only view of the open conversation's workspace: folders, a preview of
// text and images, and downloads (docs/EVERYDAY.md).
export function FilesPanel({ threadId, workspace, onClose }: { threadId: number; workspace: string | null; onClose: () => void }) {
  const [path, setPath] = useState("");
  const [listing, setListing] = useState<FolderListing | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .listWorkspaceFiles(threadId, path)
      .then((value) => {
        if (!alive) return;
        setListing(value);
        setListError(null);
      })
      .catch((err) => alive && setListError(message(err, "Could not list this folder.")));
    return () => {
      alive = false;
    };
  }, [threadId, path]);

  useEffect(() => () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
  }, [imageUrl]);

  const open = async (filePath: string) => {
    setPreview(null);
    setImageUrl(null);
    setPreviewError(null);
    try {
      const found = await api.previewWorkspaceFile(threadId, filePath);
      setPreview(found);
      if (found.kind === "image") setImageUrl(await api.workspaceImageUrl(threadId, filePath));
    } catch (err) {
      setPreviewError(message(err, "Could not open this file."));
    }
  };

  const download = async (filePath: string, name: string) => {
    setDownloading(true);
    setPreviewError(null);
    try {
      const url = await api.workspaceDownloadUrl(threadId, filePath);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
    } catch (err) {
      setPreviewError(message(err, "Could not download this file."));
    } finally {
      setDownloading(false);
    }
  };

  const crumbs = path ? path.split("/") : [];

  return (
    <div
      className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Workspace files"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="flex h-[80vh] w-full max-w-4xl flex-col rounded-lg border border-zinc-200 bg-background shadow-xl dark:border-zinc-800">
        <header className="flex items-center gap-3 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold">Files</h2>
            {workspace && <p className="truncate text-xs text-zinc-500" title={workspace}>{workspace}</p>}
          </div>
          <button onClick={onClose} aria-label="Close" className="cursor-pointer rounded px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900">
            ✕
          </button>
        </header>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <section className="flex min-h-0 flex-col border-b border-zinc-200 md:w-80 md:border-b-0 md:border-r dark:border-zinc-800">
            <nav aria-label="Folder" className="flex flex-wrap items-center gap-1 px-3 py-2 text-xs">
              <button onClick={() => setPath("")} className="cursor-pointer underline">workspace</button>
              {crumbs.map((part, i) => (
                <span key={i} className="flex items-center gap-1">
                  <span className="text-zinc-400">/</span>
                  <button onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))} className="cursor-pointer underline">
                    {part}
                  </button>
                </span>
              ))}
            </nav>
            <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 text-sm">
              {path && (
                <li>
                  <button onClick={() => setPath(parentPath(path))} className="w-full cursor-pointer rounded px-2 py-1 text-left text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-900">
                    ..
                  </button>
                </li>
              )}
              {listError && <li className="px-2 py-2 text-xs text-red-600 dark:text-red-400">{listError}</li>}
              {!listError && listing === null && <li className="px-2 py-2 text-xs text-zinc-500">Loading…</li>}
              {!listError && listing?.entries.length === 0 && (
                <li className="px-2 py-2 text-xs text-zinc-500">
                  {path ? "This folder is empty." : "The workspace is empty. Files the agent writes appear here."}
                </li>
              )}
              {!listError &&
                listing?.entries.map((entry) => (
                  <li key={entry.path}>
                    {entry.type === "hidden" ? (
                      <span className="flex items-center gap-2 px-2 py-1 text-zinc-400" title="Secret files (keys, .env, credentials) are never shown.">
                        <span aria-hidden>🔒</span>
                        <span className="truncate">{entry.name}</span>
                      </span>
                    ) : (
                      <button
                        onClick={() => (entry.type === "dir" ? setPath(entry.path) : void open(entry.path))}
                        className={`flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-900 ${
                          preview?.path === entry.path ? "bg-zinc-100 dark:bg-zinc-900" : ""
                        }`}
                      >
                        <span aria-hidden>{entry.type === "dir" ? "📁" : "📄"}</span>
                        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                        {entry.type === "file" && <span className="shrink-0 text-xs text-zinc-500">{formatBytes(entry.bytes)}</span>}
                      </button>
                    )}
                  </li>
                ))}
              {listing?.truncated && <li className="px-2 py-2 text-xs text-zinc-500">Only the first 500 entries are shown.</li>}
            </ul>
          </section>
          <section className="flex min-h-0 min-w-0 flex-1 flex-col">
            {previewError && <p role="alert" className="px-4 py-3 text-sm text-red-600 dark:text-red-400">{previewError}</p>}
            {!preview && !previewError && <p className="px-4 py-6 text-sm text-zinc-500">Choose a file to preview it.</p>}
            {preview && (
              <>
                <div className="flex items-center gap-3 border-b border-zinc-200 px-4 py-2 text-xs dark:border-zinc-800">
                  <span className="min-w-0 flex-1 truncate font-medium">{preview.path}</span>
                  <span className="text-zinc-500">{formatBytes(preview.bytes)}</span>
                  <button
                    onClick={() => void download(preview.path, preview.name)}
                    disabled={downloading}
                    className="cursor-pointer rounded-md border border-zinc-300 px-2 py-1 disabled:opacity-50 dark:border-zinc-700"
                  >
                    {downloading ? "Downloading…" : "Download"}
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  {preview.kind === "text" && (
                    <>
                      <pre className="whitespace-pre-wrap break-words font-mono text-xs">{preview.text}</pre>
                      {preview.truncated && <p className="mt-2 text-xs text-zinc-500">Only the first 256 KB is shown. Download the file for all of it.</p>}
                    </>
                  )}
                  {preview.kind === "image" &&
                    (imageUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element -- a local object URL
                      <img src={imageUrl} alt={preview.name} className="max-h-full max-w-full" />
                    ) : (
                      <p className="text-sm text-zinc-500">Loading image…</p>
                    ))}
                  {preview.kind === "binary" && <p className="text-sm text-zinc-500">This file can&apos;t be previewed. Download it to open it.</p>}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
