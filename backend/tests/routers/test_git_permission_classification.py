"""The real hook and stored policy must distinguish listings from mutations."""
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.db.models.permission_rule import PermissionRule
from app.db.session import get_db
from app.main import app


@pytest.mark.parametrize(
    "command,behavior",
    [
        ("git branch -D topic", "deny"),
        ("git branch new-topic", "deny"),
        ("git remote set-url origin https://example.invalid/changed", "deny"),
        ("git remote remove origin", "deny"),
        ("git branch --list", "allow"),
        ("git remote -v", "allow"),
    ],
)
async def test_git_hook_uses_mutation_policy(db_session, monkeypatch, command, behavior):
    owner = "git_policy_" + uuid4().hex
    db_session.add_all([
        PermissionRule(user_id=owner, bucket="read", decision="allow"),
        PermissionRule(user_id=owner, bucket="create", decision="deny"),
    ])
    await db_session.flush()

    async def session():
        yield db_session

    monkeypatch.setitem(app.dependency_overrides, get_db, session)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        response = await client.post(
            f"/api/v1/permissions/check/{owner}/1",
            headers={"Authorization": f"Bearer {settings.HOOK_TOKEN}"},
            json={"tool_name": "Bash", "tool_input": {"command": command}},
        )
    assert response.status_code == 200, response.text
    assert response.json()["hookSpecificOutput"]["decision"]["behavior"] == behavior


@pytest.mark.parametrize(
    "command",
    [
        "git status; touch x",
        "git log $(touch x)",
        "git -c core.pager=x log",
        "git diff --output=out.txt",
    ],
)
async def test_read_allow_rule_does_not_cover_other_commands(db_session, monkeypatch, command):
    owner = "git_policy_" + uuid4().hex
    db_session.add_all([
        PermissionRule(user_id=owner, bucket="read", decision="allow"),
        PermissionRule(user_id=owner, bucket="read", connector="github", decision="allow"),
        PermissionRule(user_id=owner, bucket="system", decision="deny"),
    ])
    await db_session.flush()

    async def session():
        yield db_session

    monkeypatch.setitem(app.dependency_overrides, get_db, session)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://localhost") as client:
        response = await client.post(
            f"/api/v1/permissions/check/{owner}/1",
            headers={"Authorization": f"Bearer {settings.HOOK_TOKEN}"},
            json={"tool_name": "Bash", "tool_input": {"command": command}},
        )
    assert response.status_code == 200, response.text
    assert response.json()["hookSpecificOutput"]["decision"]["behavior"] == "deny"
