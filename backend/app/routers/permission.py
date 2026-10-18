from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.permission import AnswerAskRequest, PermissionAskResponse
from app.services import notification_service, permission_ask_service
from app.services.github_classifier import classify_bash_command
from app.services.permission_service import check_permission as check_permission_rule

router = APIRouter(
    prefix="/permissions",
    tags=["Permissions"],
)

# Coarse default: no connector/tool-level granularity for these yet.
# Anything not listed here defaults to "system" — the most cautious bucket
# — rather than being assumed safe.
TOOL_BUCKETS = {
    "Read": "read",
    "Grep": "read",
    "Glob": "read",
    "WebFetch": "read",
    "WebSearch": "read",
    "Write": "create",
    "Edit": "create",
    "Bash": "system",
}


def _classify(tool_name: str, body: dict) -> tuple[str, str | None, str | None]:
    """(bucket, connector, tool) for a hook call. Bash commands are run
    through per-connector classifiers first (currently just github_classifier
    for git) — this is the one place that knowledge plugs in, so adding a
    new connector's classifier here is the only change it needs."""
    if tool_name == "Bash":
        command = body.get("tool_input", {}).get("command", "")
        classified = classify_bash_command(command)
        if classified is not None:
            return classified

    return TOOL_BUCKETS.get(tool_name, "system"), None, None

# Kept under the hook's own 120s timeout (settings.json's PermissionRequest
# config) so we always get a chance to return "deny" ourselves on expiry,
# rather than letting the hook call itself time out with no answer.
ASK_POLL_TIMEOUT_SECONDS = 110
ASK_POLL_INTERVAL_SECONDS = 2


@router.get("/health")
def health_check():
    """
    Health check endpoint for the permissions router.
    """

    return {"status": "ok"}


@router.post("/check/{user_id}/{task_id}")
async def check_permission(
    user_id: str, task_id: int, request: Request, db: AsyncSession = Depends(get_db)
):
    """
    Answer a Claude Code PermissionRequest hook call for the given user's
    task. Maps the tool being called to a risk bucket and asks the
    permission engine for a decision. A decision of "ask" blocks this call
    (via a pending permission_ask row) until a human answers from the web
    UI or Telegram, or the poll times out — returns the result in the
    shape Claude Code expects either way.
    """
    # An empty HOOK_TOKEN (Boat not configured) must never match "Bearer ".
    if not settings.HOOK_TOKEN or request.headers.get("authorization") != f"Bearer {settings.HOOK_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    tool_name = body.get("tool_name", "")
    bucket, connector, tool = _classify(tool_name, body)

    decision = await check_permission_rule(db, user_id, bucket, connector=connector, tool=tool)
    if decision == "ask":
        ask = await permission_ask_service.create_ask(
            db, user_id, bucket=bucket, task_id=task_id, connector=connector, tool=tool
        )
        chat_id = notification_service.telegram_chat_id(user_id)
        if chat_id is not None:
            await notification_service.send_permission_ask(chat_id, ask)
        decision = await permission_ask_service.poll_for_answer(
            db,
            ask.id,
            timeout_seconds=ASK_POLL_TIMEOUT_SECONDS,
            interval_seconds=ASK_POLL_INTERVAL_SECONDS,
        )

    behavior = "allow" if decision == "allow" else "deny"

    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": behavior},
        }
    }


@router.get("/ask/task/{task_id}", response_model=PermissionAskResponse | None)
async def get_pending_ask(task_id: int, db: AsyncSession = Depends(get_db)):
    """
    The ask a task is currently waiting on, if any. The web UI polls this
    to know whether to show an approval prompt for a given task.
    """
    return await permission_ask_service.get_pending_ask_for_task(db, task_id)


@router.post("/ask/{ask_id}/answer", response_model=PermissionAskResponse)
async def answer_ask(
    ask_id: int, payload: AnswerAskRequest, db: AsyncSession = Depends(get_db)
):
    """
    Answer a pending ask from the web UI. Shares its outcome with the
    Telegram answering path — both call the same service function, so
    whichever channel the user answers from, the result is identical.
    """
    try:
        return await permission_ask_service.answer_ask(
            db, ask_id, decision=payload.decision, always=payload.always
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))