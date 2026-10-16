from pathlib import Path

import pytest

from app.core.config import settings
from app.services import local_claude_runner
from app.services.thread_runtime import ProviderUnavailable
from tests.services.thread_harness import *  # noqa: F401,F403


async def test_old_and_new_threads_keep_their_workspaces_across_runtime_restart(rt, sessions, owner, cli, monkeypatch, tmp_path):
    from app.services.workspaces import canonical_workspace
    # Use the real workspace chooser rather than the harness's fixed directory.
    def choose():
        folder = canonical_workspace(settings.LOCAL_WORKSPACE_ROOT)
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    monkeypatch.setattr(local_claude_runner, "workspace_dir", choose)
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "first"))
    first = await rt.create_thread()
    await rt.send_turn(first.id, "first")
    await finished(sessions, first.id)
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOT", str(tmp_path / "second"))
    second = await rt.create_thread()
    await rt.send_turn(second.id, "second")
    await finished(sessions, second.id)
    await rt.shutdown()
    # A new runtime loads the thread from the database; no in-memory workspace state.
    from app.services.thread_runtime import ThreadRuntime
    fresh = ThreadRuntime(adapters=rt.adapters, _session_factory=sessions, owner_id=owner, status_ttl=0)
    try:
        await fresh.send_turn(first.id, "continue first")
        await finished(sessions, first.id, 2)
        await fresh.send_turn(second.id, "continue second")
        await finished(sessions, second.id, 2)
    finally:
        await fresh.shutdown()
    starts = cli.log("start")
    assert [Path(item["cwd"]) for item in starts] == [Path(first.workspace_path), Path(second.workspace_path)] * 2
    assert [item["mode"] for item in starts] == ["new", "new", "resume", "resume"]


async def test_missing_pinned_workspace_is_not_recreated(rt, sessions, owner, cli, tmp_path):
    workspace = tmp_path / "gone"
    workspace.mkdir()
    thread = await new_thread(sessions, owner, workspace_path=str(workspace))
    workspace.rmdir()
    with pytest.raises(ProviderUnavailable, match="missing or inaccessible"):
        await rt.send_turn(thread.id, "do not switch folders")
    assert not workspace.exists()
    assert cli.launches() == []
    from app.db.models import Thread
    assert (await reload(sessions, Thread, thread.id)).session_state == "new"
