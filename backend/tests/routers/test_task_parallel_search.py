import json
import shlex
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings, settings
from app.db.models.task import Tasks
from app.db.session import get_db
from app.routers.task import router
from app.services import user_credential_service
from tests.routers.test_task_launch_errors import boat_api  # noqa: F401


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("reply", [False, True])
async def test_parallel_search_task_launch(
    db_session, user_id, monkeypatch, boat_api, enabled, reply
):
    """Observe the real task router and Boat SDK's outgoing command body."""
    base_url, state = boat_api
    monkeypatch.setattr(settings, "BOAT_BASE_URL", base_url)
    monkeypatch.setattr(settings, "PARALLEL_SEARCH_ENABLED", enabled)
    await user_credential_service.save_token(
        db_session, user_id=user_id, provider="claude",
        file_path=".claude/.credentials.json",
        plaintext=json.dumps({"claudeAiOauth": {"expiresAt": 1, "accessToken": "owned-fixture"}}),
    )
    parent_id = None
    session_id = str(uuid.uuid4())
    if reply:
        parent = Tasks(
            user_id=user_id, provider="claude", box_id="bx_abcdefgh",
            prompt_text="earlier", status="succeeded", session_id=session_id,
            prompt_id="1",
        )
        db_session.add(parent)
        await db_session.flush()
        parent_id = parent.id
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    prompt = "Find Python's release; treat $(echo unsafe) and `echo unsafe` as text."
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned"
    ) as client:
        response = await client.post("/api/v1/tasks", json={
            "user_id": user_id, "provider": "claude", "box_id": "bx_abcdefgh",
            "prompt_text": prompt, "parent_task_id": parent_id,
        })
    assert response.status_code == 200, response.text
    assert state["launches"] == 1
    request = state["commands"][0]
    argv = shlex.split(request["command"])
    assert argv[:5] == ["claude", "-p", prompt, "--output-format", "json"]
    assert request["timeoutSeconds"] == 600
    assert request["detached"] is True
    flag = "--resume" if reply else "--session-id"
    assert argv[-2] == flag
    if reply:
        assert argv[-1] == session_id
    else:
        uuid.UUID(argv[-1])
    if enabled:
        assert argv[5] == "--mcp-config"
        config = json.loads(argv[6])
        assert config == {"mcpServers": {"open-dots-parallel-search": {
            "type": "http", "url": "https://search.parallel.ai/mcp",
            "headers": {"User-Agent": "OpenDots/0.1 (+https://github.com/Anil-matcha/open-dots)"},
        }}}
        assert len(argv) == 9
    else:
        assert len(argv) == 7
    hook = json.loads(state["files"][".claude/settings.json"])
    permission = hook["hooks"]["PermissionRequest"][0]
    assert permission["matcher"] == "*"
    assert permission["hooks"][0]["url"] == (
        f"{settings.PERMISSION_HOOK_BASE_URL}/api/v1/permissions/check/{user_id}/{response.json()['id']}"
    )
    assert permission["hooks"][0]["headers"] == {"Authorization": f"Bearer {settings.HOOK_TOKEN}"}


def test_parallel_search_is_opt_in(monkeypatch):
    monkeypatch.delenv("PARALLEL_SEARCH_ENABLED", raising=False)
    assert Settings(_env_file=None).PARALLEL_SEARCH_ENABLED is False
    monkeypatch.setenv("PARALLEL_SEARCH_ENABLED", "true")
    assert Settings(_env_file=None).PARALLEL_SEARCH_ENABLED is True
