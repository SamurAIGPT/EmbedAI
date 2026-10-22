"""Computer lifecycle, leases, idle stop, interrupted removals and orphans, with
a real database and the fake docker."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from app.computers.provider import ComputerError, DockerProvider
from app.computers.service import ComputerService, ComputerUnavailable, _utcnow
from app.db.models import Agent, Computer
from tests.computers.harness import IMAGE, docker  # noqa: F401
from tests.services.thread_harness import cleanup, owner, sessions  # noqa: F401


@pytest.fixture
def service(docker, sessions, owner, tmp_path):  # noqa: F811
    return ComputerService(
        providers={"docker": DockerProvider(docker=str(docker.path))},
        owner_id=owner,
        root=tmp_path / "computers",
        _session_factory=sessions,
    )


@pytest.fixture(autouse=True)
async def computer_cleanup(sessions, owner):  # noqa: F811
    yield
    async with sessions() as db:
        await db.execute(delete(Computer).where(Computer.owner_id == owner))
        await db.execute(delete(Agent).where(Agent.owner_id == owner))
        await db.commit()


@pytest.fixture
async def agent(sessions, owner):  # noqa: F811
    async with sessions() as db:
        row = Agent(owner_id=owner, name="Builder", instructions="")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


@pytest.fixture
def workspace(tmp_path):
    folder = tmp_path / "agent-workspace"
    folder.mkdir()
    return str(folder)


async def test_making_the_row_is_idempotent_and_touches_no_docker(service, agent, workspace, docker):  # noqa: F811
    first, created = await service.create_for_agent(agent, workspace=workspace, cpus=2, network="default")
    again, created_again = await service.create_for_agent(agent, workspace=workspace, cpus=4)
    assert created and not created_again and again.id == first.id
    assert first.cpu_millis == 2000 and first.network == "default" and first.state == "missing"
    assert first.image == IMAGE
    assert docker.calls() == []


async def test_bad_settings_are_refused(service, agent, workspace):
    with pytest.raises(ValueError):
        await service.create_for_agent(agent, workspace=workspace, network="host")
    with pytest.raises(ValueError):
        await service.create_for_agent(agent, workspace=workspace, memory_mb=1)


async def test_start_makes_and_runs_the_machine_from_a_local_image(service, agent, workspace, docker):  # noqa: F811
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    with pytest.raises(ComputerError) as raised:
        await service.start(computer.id)
    assert raised.value.code == "image_missing"
    assert (await service.get(computer.id)).state == "error"
    docker.add_image()
    computer = await service.start(computer.id)
    assert computer.state == "running" and computer.container_id
    assert (await service.stop(computer.id)).state == "stopped"
    assert (await service.start(computer.id)).state == "running"
    assert docker.commands().count("run") == 1


async def test_a_workspace_docker_cannot_mount_is_a_refusal_not_a_stuck_computer(service, agent, tmp_path, docker):  # noqa: F811
    folder = tmp_path / "client a,b"
    folder.mkdir()
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=str(folder))
    with pytest.raises(ComputerError) as raised:
        await service.start(computer.id)
    assert raised.value.code == "invalid_computer" and "comma" in str(raised.value)
    after = await service.get(computer.id)
    assert after.state == "error" and "comma" in after.last_error
    assert "run" not in docker.commands()
    with pytest.raises(ComputerUnavailable, match="comma"):
        await service.attach_for_turn(agent.id, 1, str(folder))


async def test_prepare_image_is_the_only_download(service, agent, workspace, docker):  # noqa: F811
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    await service.prepare_image(computer.id)
    assert "pull" in docker.commands()
    assert (await service.start(computer.id)).state == "running"


async def test_docker_down_is_a_clear_refusal(service, agent, workspace, docker):  # noqa: F811
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    docker.mode("down")
    with pytest.raises(ComputerError) as raised:
        await service.start(computer.id)
    assert raised.value.code == "docker_unavailable"
    health, status = await service.live_status(await service.get(computer.id))
    assert not health.available and status is None


async def test_a_turn_starts_a_stopped_computer_and_holds_it(service, agent, workspace, docker):  # noqa: F811
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    binding = await service.attach_for_turn(agent.id, 101, workspace)
    assert binding is not None and binding.name in docker.state()["containers"]
    assert (await service.get(computer.id)).state == "running"
    assert binding.launch.args[binding.launch.args.index("--container") + 1] == binding.name
    with pytest.raises(ComputerError) as raised:
        await service.stop(computer.id)
    assert raised.value.code == "computer_busy"
    await service.release_turn(binding, 101)
    assert not binding.grants_dir.exists()
    assert (await service.stop(computer.id)).state == "stopped"


async def test_a_turn_never_falls_back_when_the_computer_cannot_be_used(service, agent, workspace, docker, tmp_path):  # noqa: F811
    assert await service.attach_for_turn(agent.id, 1, workspace) is None  # no computer: host as before
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    with pytest.raises(ComputerUnavailable, match="Prepare the image"):
        await service.attach_for_turn(agent.id, 1, workspace)
    docker.add_image()
    with pytest.raises(ComputerUnavailable, match="not the one mounted"):
        await service.attach_for_turn(agent.id, 1, str(tmp_path))
    await service.update_config(computer.id, auto_start=False)
    with pytest.raises(ComputerUnavailable, match="automatically is off"):
        await service.attach_for_turn(agent.id, 1, workspace)
    docker.mode("down")
    with pytest.raises(ComputerUnavailable, match="cannot be reached"):
        await service.attach_for_turn(agent.id, 1, workspace)
    assert not service.busy(computer.id)


async def test_limits_apply_live_and_image_or_network_need_a_recreate(service, agent, workspace, docker):  # noqa: F811
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    computer = await service.start(computer.id)
    computer = await service.update_config(computer.id, cpus=2, memory_mb=2048)
    assert "update" in docker.commands()
    computer = await service.update_config(computer.id, network="default")
    _, status = await service.live_status(computer)
    assert status.needs_recreate
    computer = await service.recreate(computer.id)
    _, status = await service.live_status(computer)
    assert computer.state == "running" and not status.needs_recreate
    assert docker.commands().count("run") == 2


async def test_idle_computers_are_stopped_but_not_while_a_turn_holds_them(service, agent, workspace, docker, sessions):  # noqa: F811
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=workspace, idle_minutes=5)
    await service.start(computer.id)
    async with sessions() as db:
        row = await db.get(Computer, computer.id)
        row.last_used_at = _utcnow() - timedelta(minutes=10)
        row.updated_at = _utcnow() - timedelta(minutes=10)
        await db.commit()
    service._leases[computer.id] = {7}
    await service.upkeep()
    assert (await service.get(computer.id)).state == "running"
    service._leases.clear()
    await service.upkeep()
    assert (await service.get(computer.id)).state == "stopped"


async def test_a_removal_cut_short_is_finished_later(service, agent, workspace, docker, sessions):  # noqa: F811
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    computer = await service.start(computer.id)
    name = service.spec(computer).name
    docker.mode("down")
    assert await service.delete(computer.id) is False
    async with sessions() as db:
        row = await db.get(Computer, computer.id)
    assert row.state == "deleting" and "waiting" in row.last_error
    assert await service.for_agent(agent.id) is None  # no longer offered
    docker.mode("")
    await service.upkeep()
    async with sessions() as db:
        assert await db.get(Computer, computer.id) is None
    assert docker.container(name) is None
    assert (await service.for_agent(agent.id)) is None


async def test_deleting_keeps_the_workspace_files(service, agent, workspace, docker):  # noqa: F811
    from pathlib import Path

    docker.add_image()
    (Path(workspace) / "keep.txt").write_text("mine")
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    await service.start(computer.id)
    assert await service.delete(computer.id) is True
    assert (Path(workspace) / "keep.txt").read_text() == "mine"


async def test_orphans_of_this_installation_are_removed_once_docker_answers(service, agent, workspace, docker, sessions):  # noqa: F811
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    computer = await service.start(computer.id)
    kept = service.spec(computer).name
    # A machine whose row is gone (say, the database was restored from a backup).
    from app.computers.docker_cli import Spec

    orphan = Spec(computer_id=999_999, instance=service.instance(), image=IMAGE, workspace=workspace)
    service.provider().create(orphan)
    # Another installation's machine on the same Docker.
    other = Spec(computer_id=999_999, instance="e" * 32, image=IMAGE, workspace=workspace)
    service.provider().create(other)
    docker.mode("down")
    await service.upkeep()
    assert docker.container(orphan.name) is not None
    docker.mode("")
    await service.upkeep()
    assert docker.container(orphan.name) is None
    assert docker.container(kept) is not None and docker.container(other.name) is not None


async def test_removing_the_agent_removes_its_computer(service, agent, workspace, docker, sessions):  # noqa: F811
    docker.add_image()
    computer, _ = await service.create_for_agent(agent, workspace=workspace)
    computer = await service.start(computer.id)
    await service.mark_agent_deleted(agent.id)
    async with sessions() as db:
        assert (await db.execute(select(Computer).where(Computer.id == computer.id))).scalar_one_or_none() is None
    assert docker.state()["containers"] == {}
