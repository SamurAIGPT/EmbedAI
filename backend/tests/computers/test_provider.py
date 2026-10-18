"""The Docker provider against a fake docker executable (tests/computers/fake_docker.py)."""

import pytest

from app.computers import docker_cli
from app.computers.docker_cli import Spec
from app.computers.provider import ComputerError, DockerProvider, load_instance_id
from tests.computers.harness import IMAGE, INSTANCE, docker  # noqa: F401


@pytest.fixture
def provider(docker):  # noqa: F811
    return DockerProvider(docker=str(docker.path))


@pytest.fixture
def spec(tmp_path):
    folder = tmp_path / "workspace"
    folder.mkdir()
    return Spec(computer_id=3, instance=INSTANCE, image=IMAGE, workspace=str(folder), user="501:20")


def test_health_reports_a_running_docker(provider):
    health = provider.health()
    assert health.available and health.version == "27.0.0-fake"


def test_health_degrades_when_docker_is_down_slow_or_missing(provider, docker, monkeypatch):  # noqa: F811
    docker.mode("down")
    health = provider.health()
    assert not health.available and "not running" in health.error
    docker.mode("hang")
    monkeypatch.setattr(DockerProvider, "INFO_TIMEOUT", 1.0)
    health = provider.health()
    assert not health.available and "not responding" in health.error
    missing = DockerProvider(docker="/nonexistent/docker").health()
    assert not missing.available and "not installed" in missing.error.lower() or "could not be started" in missing.error


def test_create_never_downloads_an_image(provider, docker, spec):  # noqa: F811
    with pytest.raises(ComputerError) as raised:
        provider.create(spec)
    assert raised.value.code == "image_missing"
    assert "pull" not in docker.commands() and "run" not in docker.commands()


def test_create_is_idempotent(provider, docker, spec):  # noqa: F811
    docker.add_image()
    first = provider.create(spec)
    assert first.state == "running" and not first.needs_recreate
    second = provider.create(spec)
    assert second.state == "running" and second.container_id == first.container_id
    assert docker.commands().count("run") == 1


def test_create_wakes_a_stopped_computer_instead_of_making_another(provider, docker, spec):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    assert provider.stop(spec).state == "stopped"
    assert provider.create(spec).state == "running"
    assert docker.commands().count("run") == 1


def test_a_container_someone_else_made_under_the_name_is_refused_and_kept(provider, docker, spec):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    docker.edit_container(spec.name, lambda c: c["Config"]["Labels"].clear())
    assert provider.status(spec).state == "foreign"
    for step in (provider.create, provider.start, provider.delete):
        with pytest.raises(ComputerError):
            step(spec)
    assert docker.container(spec.name) is not None


def test_a_computer_changed_by_hand_is_refused(provider, docker, spec):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    docker.edit_container(spec.name, lambda c: c["HostConfig"].update(CapDrop=[]))
    status = provider.status(spec)
    assert status.state == "unsafe" and "capabilities" in status.problem
    docker.edit_container(spec.name, lambda c: c["HostConfig"].update(CapDrop=["ALL"], PortBindings={"22/tcp": [{"HostPort": "2222"}]}))
    assert "ports" in provider.status(spec).problem
    docker.edit_container(spec.name, lambda c: (c["HostConfig"].update(PortBindings={}), c["Mounts"].append({"Type": "bind", "Destination": "/host"})))
    assert "mounted" in provider.status(spec).problem
    with pytest.raises(ComputerError):
        provider.create(spec)


def test_changed_image_or_network_asks_for_a_recreate(provider, docker, spec):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    moved = Spec(**{**spec.__dict__, "network": "default"})
    assert provider.status(moved).needs_recreate


def test_limits_change_on_a_live_computer(provider, docker, spec):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    provider.update_limits(Spec(**{**spec.__dict__, "cpus": 2.0, "memory_mb": 512}))
    assert docker.container(spec.name)["inspect"]["HostConfig"]["Updated"] == [
        "--cpus", "2", "--memory", "512m", "--memory-swap", "512m", "--pids-limit", "256",
    ]


def test_delete_removes_only_its_own_and_is_quiet_when_gone(provider, docker, spec):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    provider.delete(spec)
    assert docker.container(spec.name) is None
    provider.delete(spec)  # already gone: nothing to do


def test_managed_computers_are_listed_per_installation(provider, docker, spec, tmp_path):  # noqa: F811
    docker.add_image()
    provider.create(spec)
    other = Spec(computer_id=3, instance="f" * 32, image=IMAGE, workspace=spec.workspace, user="501:20")
    provider.create(other)
    assert provider.list_managed(INSTANCE) == [(spec.name, 3)]
    with pytest.raises(ComputerError):
        provider.remove_by_name(other.name, INSTANCE)
    provider.remove_by_name(spec.name, INSTANCE)
    assert docker.container(spec.name) is None and docker.container(other.name) is not None


def test_the_mcp_launch_names_the_container_user_and_folders(provider, spec, tmp_path):
    launch = provider.mcp_launch(spec, tmp_path / "grants", tmp_path / "state")
    assert launch.args[:2] == ["-m", "app.computers.mcp_server"]
    assert launch.args[launch.args.index("--container") + 1] == spec.name
    assert launch.args[launch.args.index("--user") + 1] == "501:20"
    assert "PYTHONPATH" in launch.env


def test_the_installation_id_is_stable_and_private(tmp_path):
    first = load_instance_id(tmp_path / "computers")
    assert first == load_instance_id(tmp_path / "computers")
    assert len(first) == 32 and docker_cli.container_name(first, 1).startswith("open-dots-computer-")
