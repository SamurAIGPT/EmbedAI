"""Browse and download a conversation's workspace files (F08, docs/EVERYDAY.md).

Admin scope only (app/auth/policy.py does not list these routes for `chat`).
"""

import os
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.routers.threads import _thread_or_404, get_runtime
from app.services import workspace_files as files
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/threads", tags=["Workspace files"], dependencies=[Depends(require_local_access)])

_SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "sandbox",
    "Cache-Control": "no-store",
}


class FileEntry(BaseModel):
    name: str
    # Relative to the workspace root, with forward slashes.
    path: str
    # dir | file | hidden (a secret file: listed, never read)
    type: str
    bytes: int | None
    modified: datetime | None


class FolderListing(BaseModel):
    path: str
    entries: list[FileEntry]
    truncated: bool


class FilePreview(BaseModel):
    path: str
    name: str
    bytes: int
    mime: str
    # text | image | binary
    kind: str
    # The first 256 KB of a text file; null otherwise.
    text: str | None = None
    truncated: bool = False


def _refuse(error: files.FileAccessError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error_code": error.code, "message": error.message})


async def _workspace(rt: ThreadRuntime, thread_id: int) -> str | None:
    return (await _thread_or_404(rt, thread_id)).workspace_path


@router.get("/{thread_id}/files", response_model=FolderListing)
async def list_files(
    thread_id: int,
    path: str = Query("", max_length=files.PATH_MAX_CHARS),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """One folder of the conversation's workspace: folders first, then files.
    Links that lead outside the workspace are left out; secret files are
    listed as `hidden`."""
    workspace = await _workspace(rt, thread_id)
    try:
        rel, entries, truncated = await run_in_threadpool(files.list_dir, workspace, path)
    except files.FileAccessError as error:
        raise _refuse(error) from None
    return FolderListing(
        path=rel,
        truncated=truncated,
        entries=[
            FileEntry(
                name=e.name,
                path=e.path,
                type=e.type,
                bytes=e.bytes,
                modified=datetime.fromtimestamp(e.modified, UTC) if e.modified is not None else None,
            )
            for e in entries
        ],
    )


@router.get("/{thread_id}/files/content", response_model=FilePreview)
async def preview_file(
    thread_id: int,
    path: str = Query(..., min_length=1, max_length=files.PATH_MAX_CHARS),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """A preview: the start of a text file, or the metadata of an image (fetch
    its bytes from /files/raw)."""
    workspace = await _workspace(rt, thread_id)
    try:
        found = await run_in_threadpool(files.preview, workspace, path)
    except files.FileAccessError as error:
        raise _refuse(error) from None
    opened = found["file"]
    return FilePreview(
        path=opened.path,
        name=opened.name,
        bytes=opened.bytes,
        mime=opened.mime,
        kind=found["kind"],
        text=found.get("text"),
        truncated=opened.truncated if found["kind"] == "text" else False,
    )


@router.get("/{thread_id}/files/raw")
async def raw_image(
    thread_id: int,
    path: str = Query(..., min_length=1, max_length=files.PATH_MAX_CHARS),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """An image's bytes for the preview (PNG, JPEG, GIF, WebP up to 10 MB)."""
    workspace = await _workspace(rt, thread_id)
    try:
        opened = await run_in_threadpool(
            files.read_file, workspace, path, max_bytes=files.IMAGE_PREVIEW_BYTES, whole=True
        )
    except files.FileAccessError as error:
        raise _refuse(error) from None
    if opened.mime not in files.IMAGE_MIMES:
        raise _refuse(files.FileAccessError(415, "not_an_image", "Only images are shown here; download other files."))
    return Response(content=opened.data, media_type=opened.mime, headers=_SAFE_HEADERS)


@router.get("/{thread_id}/files/download")
async def download_file(
    thread_id: int,
    path: str = Query(..., min_length=1, max_length=files.PATH_MAX_CHARS),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """The file as a download (up to 50 MB), never rendered by the browser."""
    workspace = await _workspace(rt, thread_id)
    try:
        opened = await run_in_threadpool(
            files.read_file, workspace, path, max_bytes=files.DOWNLOAD_MAX_BYTES, whole=True
        )
    except files.FileAccessError as error:
        raise _refuse(error) from None
    return Response(
        content=opened.data,
        media_type="application/octet-stream",
        headers={**_SAFE_HEADERS, "Content-Disposition": files.download_name(opened.name)},
    )


@router.get("/{thread_id}/files/zip")
async def download_folder(
    thread_id: int,
    path: str = Query("", max_length=files.PATH_MAX_CHARS),
    rt: ThreadRuntime = Depends(get_runtime),
):
    """A folder (the whole workspace when `path` is empty) as a zip download.
    Secret files, links out of the workspace and names unsafe in a zip are
    left out (`X-Skipped-Unsafe-Names` counts the last); links to folders are
    not followed; over 2000 files or 200 MB in total is 413."""
    workspace = await _workspace(rt, thread_id)
    try:
        name, archive, skipped = await run_in_threadpool(files.zip_folder, workspace, path)
    except files.FileAccessError as error:
        raise _refuse(error) from None
    return FileResponse(
        archive,
        media_type="application/zip",
        headers={
            **_SAFE_HEADERS,
            "Content-Disposition": files.download_name(name),
            # Files and folders left out because their names are unsafe in a zip.
            "X-Skipped-Unsafe-Names": str(skipped),
        },
        background=BackgroundTask(os.unlink, archive),
    )
