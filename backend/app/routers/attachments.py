"""Files attached to messages (docs/EVERYDAY.md)."""

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

from app.routers.threads import _thread_or_404, get_runtime
from app.services import attachments
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/threads", tags=["Attachments"], dependencies=[Depends(require_local_access)])

_READ_LIMIT = max(attachments.IMAGE_MAX_BYTES, attachments.TEXT_MAX_BYTES) + 1


class AttachmentResponse(BaseModel):
    id: str
    name: str
    mime: str
    bytes: int
    # image | text
    kind: str


def _refuse(error: attachments.AttachmentError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error_code": error.code, "message": error.message})


@router.post("/{thread_id}/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    thread_id: int, file: UploadFile = File(...), rt: ThreadRuntime = Depends(get_runtime)
):
    """Store one file for this conversation's next message. Send its id in
    `attachment_ids` with the turn."""
    await _thread_or_404(rt, thread_id)
    data = await file.read(_READ_LIMIT)
    if len(data) >= _READ_LIMIT:
        raise _refuse(
            attachments.AttachmentError(
                413, "too_large", f"{attachments.display_name(file.filename)} is over the 10 MB limit."
            )
        )
    try:
        saved = await run_in_threadpool(attachments.save, thread_id, file.filename, file.content_type, data)
    except attachments.AttachmentError as error:
        raise _refuse(error) from None
    return AttachmentResponse(**saved.public())


@router.get("/{thread_id}/attachments/{attachment_id}")
async def get_attachment(thread_id: int, attachment_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """The attached file's bytes. Images are shown inline; text is served as
    plain text. Never interpreted by the browser as a page."""
    await _thread_or_404(rt, thread_id)
    try:
        attachment, data = await run_in_threadpool(attachments.read_bytes, thread_id, attachment_id)
    except attachments.AttachmentError as error:
        raise _refuse(error) from None
    media = attachment.mime if attachment.kind == "image" else "text/plain; charset=utf-8"
    return Response(
        content=data,
        media_type=media,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )
