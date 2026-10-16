"""Saved "always allow" rules for Bash commands, managed by the person.

Local access only. A rule only skips the question for a call that matches it;
see services/approval_allowlist.py for exactly what matches."""

from fastapi import APIRouter, Depends, HTTPException

from app.routers.threads import get_runtime
from app.schemas.approvals import ApprovalRuleCreate, ApprovalRuleResponse
from app.services import approval_allowlist as allowlist
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(
    prefix="/approval-rules",
    tags=["Approvals"],
    dependencies=[Depends(require_local_access)],
)


def _public(rule) -> ApprovalRuleResponse:
    match, workspace = allowlist.split_scope(rule.connector)
    return ApprovalRuleResponse(
        id=rule.id, match=match, command=rule.tool, workspace=workspace, created_at=rule.created_at
    )


@router.get("", response_model=list[ApprovalRuleResponse])
async def list_rules(rt: ThreadRuntime = Depends(get_runtime)):
    async with rt.session() as db:
        return [_public(r) for r in await allowlist.list_rules(db, rt.owner_id)]


@router.post("", response_model=ApprovalRuleResponse, status_code=201)
async def add_rule(payload: ApprovalRuleCreate, rt: ThreadRuntime = Depends(get_runtime)):
    try:
        async with rt.session() as db:
            rule = await allowlist.add_rule(
                db, rt.owner_id, payload.match, payload.command, payload.workspace
            )
    except allowlist.InvalidRule as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _public(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, rt: ThreadRuntime = Depends(get_runtime)):
    async with rt.session() as db:
        if not await allowlist.delete_rule(db, rt.owner_id, rule_id):
            raise HTTPException(status_code=404, detail="Rule not found")
