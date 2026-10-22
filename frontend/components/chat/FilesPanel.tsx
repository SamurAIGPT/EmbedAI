"use client";

import { Dialog } from "@/components/ui/Dialog";
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
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title="Files" description={workspace ?? undefined} size="lg" flush>
        <div className="flex h-[min(68dvh,40rem)] min-h-0 flex-col md:flex-row">
          <section className="flex min-h-0 flex-col border-b border-line md:w-80 md:border-b-0 md:border-r">
            <nav aria-label="Folder" className="flex flex-wrap items-center gap-1 px-3 py-2 text-xs">
              <button onClick={() => setPath("")} className="cursor-pointer underline">workspace</button>
              {crumbs.map((part, i) => (
                <span key={i} className="flex items-center gap-1">
                  <span className="text-faint">/</span>
                  <button onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))} className="cursor-pointer underline">
                    {part}
                  </button>
                </span>
              ))}
            </nav>
            <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 text-sm">
              {path && (
                <li>
                  <button onClick={() => setPath(parentPath(path))} className="w-full cursor-pointer rounded px-2 py-1 text-left text-muted hover:bg-hover">
                    ..
                  </button>
                </li>
              )}
              {listError && <li className="px-2 py-2 text-xs text-danger">{listError}</li>}
              {!listError && listing === null && <li className="px-2 py-2 text-xs text-muted">Loading…</li>}
              {!listError && listing?.entries.length === 0 && (
                <li className="px-2 py-2 text-xs text-muted">
                  {path ? "This folder is empty." : "The workspace is empty. Files the agent writes appear here."}
                </li>
              )}
              {!listError &&
                listing?.entries.map((entry) => (
                  <li key={entry.path}>
                    {entry.type === "hidden" ? (
                      <span className="flex items-center gap-2 px-2 py-1 text-faint" title="Secret files (keys, .env, credentials) are never shown.">
                        <span aria-hidden>🔒</span>
                        <span className="truncate">{entry.name}</span>
                      </span>
                    ) : (
                      <button
                        onClick={() => (entry.type === "dir" ? setPath(entry.path) : void open(entry.path))}
                        className={`flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1 text-left hover:bg-hover ${
                          preview?.path === entry.path ? "bg-inset" : ""
                        }`}
                      >
                        <span aria-hidden>{entry.type === "dir" ? "📁" : "📄"}</span>
                        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                        {entry.type === "file" && <span className="shrink-0 text-xs text-muted">{formatBytes(entry.bytes)}</span>}
                      </button>
                    )}
                  </li>
                ))}
              {listing?.truncated && <li className="px-2 py-2 text-xs text-muted">Only the first 500 entries are shown.</li>}
            </ul>
          </section>
          <section className="flex min-h-0 min-w-0 flex-1 flex-col">
            {previewError && <p role="alert" className="px-4 py-3 text-sm text-danger">{previewError}</p>}
            {!preview && !previewError && <p className="px-4 py-6 text-sm text-muted">Choose a file to preview it.</p>}
            {preview && (
              <>
                <div className="flex items-center gap-3 border-b border-line px-4 py-2 text-xs">
                  <span className="min-w-0 flex-1 truncate font-medium">{preview.path}</span>
                  <span className="text-muted">{formatBytes(preview.bytes)}</span>
                  <button
                    onClick={() => void download(preview.path, preview.name)}
                    disabled={downloading}
                    className="cursor-pointer rounded-md border border-line px-2 py-1 disabled:opacity-50"
                  >
                    {downloading ? "Downloading…" : "Download"}
                  </button>
                </div>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  {preview.kind === "text" && (
                    <>
                      <pre className="whitespace-pre-wrap break-words font-mono text-xs">{preview.text}</pre>
                      {preview.truncated && <p className="mt-2 text-xs text-muted">Only the first 256 KB is shown. Download the file for all of it.</p>}
                    </>
                  )}
                  {preview.kind === "image" &&
                    (imageUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element -- a local object URL
                      <img src={imageUrl} alt={preview.name} className="max-h-full max-w-full" />
                    ) : (
                      <p className="text-sm text-muted">Loading image…</p>
                    ))}
                  {preview.kind === "binary" && <p className="text-sm text-muted">This file can&apos;t be previewed. Download it to open it.</p>}
                </div>
              </>
            )}
          </section>
        </div>
    </Dialog>
  );
}
