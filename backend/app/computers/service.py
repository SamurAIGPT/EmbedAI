"""Agent computers: configuration, lifecycle, turns and upkeep.

Lifecycle (docs/COMPUTERS.md):

    (no row) --create--> missing --start--> running --stop--> stopped
                            ^                  |   <--start--    |
                            +----recreate------+                 |
    any state --delete--> deleting --(machine removed)--> (row gone)

- Making the row never touches Docker. Starting a `missing` computer makes the
  machine from an image that is already on this computer; downloading an image
  is a separate, explicit step (`prepare_image`).
- Every lifecycle step for one computer runs under that computer's lock, and
  creating is idempotent: an existing machine of this app with this identity is
  kept, a machine someone else made under the same name is refused.
- `deleting` is committed before the machine is removed and the row is removed
  only after, so a removal cut short (a crash, Docker stopped) is finished by
  the upkeep job instead of leaving an orphan.
- A running turn holds a lease on its agent's computer. Upkeep stops a running
  computer that has had no lease and no command for `idle_minutes`.
- At startup (and when Docker first answers) machines this installation made
  that no row knows of are removed.
"""

import asyncio
import json
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.computers import docker_cli, screen
from app.computers.control import ControlRegistry
from app.computers.docker_cli import Spec
from app.computers.mcp_server import LAST_COMMAND_FILE
from app.computers.policy import COMPUTER_TOOL_PREFIX
from app.computers.provider import ComputerError, DockerProvider, Health, McpLaunch, Status, load_instance_id
from app.core.logging import get_logger
from app.db.models import Agent, Computer
from app.db.transactions import begin_write
from app.desktop import profile

logger = get_logger(__name__)

HEALTH_TTL_SECONDS = 10.0
__all__ = ["COMPUTER_TOOL_PREFIX", "ComputerService", "TurnComputer"]


class ComputerNotFound(Exception):
    pass


class ComputerUnavailable(Exception):
    """The agent has a computer but this turn cannot use it. The turn fails:
    it never falls back to running on the host."""


@dataclass
class TurnComputer:
    """What a turn needs to reach its agent's computer."""

    computer_id: int
    name: str
    image: str
    provider: str
    launch: McpLaunch
    grants_dir: Path
    kind: str = "shell"
    guest_workspace: str = docker_cli.GUEST_WORKSPACE

    def prompt_note(self) -> str:
        if self.provider == "native":
            return ("You have explicitly enabled native desktop control of the person's Mac. "
                    "Use only native computer tools; no host shell or file tools are available. "
                    "Take a fresh native desktop or window snapshot before every input. Prefer element tokens from window snapshots. "
                    "Input needs approval. Stop when the person takes control; after hand-back take a fresh snapshot. "
                    "This is a shared host desktop, so actions can affect apps outside your workspace. Never substitute Docker or shell tools.")
        screen = (" You also have a 1280x800 screen. Use screenshot, click, double_click, move, drag, type, key, scroll and wait. Screen input needs permission for this turn. A person may take control; stop when refused and take a fresh screenshot after hand-back." if self.kind == "desktop" else "")
        return screen + (
            "You are working on your own computer: an isolated Linux machine "
            f"({self.provider}, image {self.image}). Use the computer tools (exec, read_file, "
            f"write_file, list_files) for every command and file. Your workspace folder is "
            f"{self.guest_workspace} there. Nothing runs on the person's own machine, and each "
            "computer call needs the person's approval (or a saved rule)."
        )


def state_root() -> Path:
    desktop = profile.current()
    if desktop is not None:
        return desktop.data_dir / "computers"
    return Path.home() / ".open-dots" / "computers"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class ComputerService:
    providers: dict = field(default_factory=lambda: {"docker": DockerProvider()})
    owner_id: str = "local-owner"
    root: Path | None = None
    _session_factory: object | None = None
    _locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    _leases: dict[int, set[int]] = field(default_factory=dict)
    _health: tuple[float, Health] | None = None
    _instance: str | None = None
    _orphans_checked: bool = False
    control: ControlRegistry = field(default_factory=ControlRegistry)
    _captures: dict = field(default_factory=dict)
    _capture_cache: dict = field(default_factory=dict)
    _capture_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(3))
    _capture_tokens: float = 4
    _capture_at: float = field(default_factory=time.monotonic)
    _control_initialized: bool = False

    def _initialize_control(self):
        if not self._control_initialized:
            for path in self.state_dir().glob("*/control.json"):
                path.unlink(missing_ok=True)
            self._control_initialized = True

    def expire_control(self):
        self._initialize_control()
        expired = self.control.expire()
        # Snapshot/owns may already have expired a registry entry.
        for path in self.state_dir().glob("*/control.json"):
            if path.parent.name.isdigit() and not self.control.snapshot(int(path.parent.name))["held"]:
                path.unlink(missing_ok=True)
        return expired

    def held(self, computer_id: int) -> bool:
        self.expire_control()
        return self.control.snapshot(computer_id)["held"]

    def release_control(self, computer_id: int, lease=None):
        self.expire_control()
        released = self.control.release(computer_id, lease)
        if released:
            (self.state_dir() / str(computer_id) / "control.json").unlink(missing_ok=True)
        return released

    def write_control(self, computer_id: int):
        # Same cross-process lock as screen input: a completed take response
        # guarantees all earlier input has finished before the person drives.
        with screen.driver_lock(self.state_dir() / str(computer_id)):
            screen.write_state(self.state_dir() / str(computer_id) / "control.json", {"until": time.time()+35})

    async def screenshot(self, computer: Computer) -> bytes:
        if computer.kind != "desktop":
            raise ComputerError("This computer has no screen.", "not_a_desktop")
        cached = self._capture_cache.get(computer.id)
        if cached and time.monotonic()-cached[0] < 1:
            return cached[1]
        task = self._captures.get(computer.id)
        if task is None:
            async def capture():
                now = time.monotonic()
                self._capture_tokens = min(4, self._capture_tokens + (now-self._capture_at)*4)
                self._capture_at = now
                if self._capture_tokens < 1:
                    raise ComputerError("Screen capture budget reached. Try again shortly.", "capture_busy")
                self._capture_tokens -= 1
                async with self._capture_slots:
                    async with self._lock(computer.id):
                        current = await self.get(computer.id)
                        data = await self._call(self.provider(current.provider).screenshot, self.spec(current))
                    self._capture_cache[computer.id] = (time.monotonic(), data)
                    return data
            task = asyncio.create_task(capture())
            self._captures[computer.id] = task
            task.add_done_callback(lambda t: self._captures.pop(computer.id, None))
        return await asyncio.shield(task)

    # -- plumbing ----------------------------------------------------------------------

    def session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal()

    def state_dir(self) -> Path:
        return self.root or state_root()

    def instance(self) -> str:
        if self._instance is None:
            self._instance = load_instance_id(self.state_dir())
        return self._instance

    def provider(self, key: str = "docker"):
        provider = self.providers.get(key)
        if provider is None:
            raise ComputerError(f"Unknown computer provider: {key}", "unknown_provider")
        return provider

    def _lock(self, computer_id: int) -> asyncio.Lock:
        return self._locks.setdefault(computer_id, asyncio.Lock())

    def spec(self, computer: Computer) -> Spec:
        return Spec(
            computer_id=computer.id,
            instance=self.instance(),
            image=computer.image,
            workspace=computer.workspace_path or "",
            cpus=computer.cpu_millis / 1000,
            memory_mb=computer.memory_mb,
            pids_limit=computer.pids_limit,
            network=computer.network,
            kind=computer.kind,
            shm_mb=512 if computer.kind == "desktop" else 0,
        )

    async def _call(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    async def health(self, *, fresh: bool = False) -> Health:
        now = time.monotonic()
        if not fresh and self._health is not None and now - self._health[0] < HEALTH_TTL_SECONDS:
            return self._health[1]
        health = await self._call(self.provider().health)
        self._health = (now, health)
        return health

    # -- rows --------------------------------------------------------------------------

    async def get(self, computer_id: int) -> Computer:
        async with self.session() as db:
            computer = await db.get(Computer, computer_id)
        if computer is None or computer.owner_id != self.owner_id or computer.state == "deleting":
            raise ComputerNotFound(computer_id)
        return computer

    async def for_agent(self, agent_id: int) -> Computer | None:
        async with self.session() as db:
            computer = (
                await db.execute(select(Computer).where(Computer.agent_id == agent_id))
            ).scalar_one_or_none()
        if computer is None or computer.owner_id != self.owner_id or computer.state == "deleting":
            return None
        return computer

    async def for_agents(self, agent_ids: list[int]) -> dict[int, Computer]:
        if not agent_ids:
            return {}
        async with self.session() as db:
            rows = (
                await db.execute(
                    select(Computer).where(
                        Computer.agent_id.in_(agent_ids),
                        Computer.owner_id == self.owner_id,
                        Computer.state != "deleting",
                    )
                )
            ).scalars()
            return {row.agent_id: row for row in rows}

    async def list_all(self) -> list[Computer]:
        async with self.session() as db:
            rows = (
                await db.execute(
                    select(Computer)
                    .where(Computer.owner_id == self.owner_id, Computer.state != "deleting")
                    .order_by(Computer.id)
                )
            ).scalars()
            return list(rows)

    async def _save(self, computer_id: int, **values) -> Computer:
        async with self.session() as db:
            await begin_write(db)
            computer = await db.get(Computer, computer_id)
            if computer is None:
                await db.rollback()
                raise ComputerNotFound(computer_id)
            for key, value in values.items():
                setattr(computer, key, value)
            computer.updated_at = _utcnow()
            await db.commit()
            await db.refresh(computer)
            return computer

    async def create_for_agent(self, agent: Agent, *, workspace: str, image: str | None = None, **config) -> tuple[Computer, bool]:
        """The agent's computer row, made if it has none. Returns (computer,
        created). Never touches the provider. Raises ValueError for bad config."""
        kind = config.pop("kind", "shell")
        if kind not in docker_cli.COMPUTER_KINDS:
            raise ValueError("kind must be shell or desktop")
        image = docker_cli.check_image(image or (docker_cli.DESKTOP_IMAGE if kind == "desktop" else docker_cli.DEFAULT_IMAGE))
        if kind == "desktop":
            config = {"cpus": 2, "memory_mb": 2048, "pids_limit": 1024, **config}
        values = _checked_config(config)
        async with self.session() as db:
            await begin_write(db)
            existing = (
                await db.execute(select(Computer).where(Computer.agent_id == agent.id))
            ).scalar_one_or_none()
            if existing is not None:
                db.expunge(existing)  # a rollback would expire it
                await db.rollback()
                if existing.state == "deleting":
                    raise ComputerError("This agent's previous computer is still being removed. Try again shortly.", "computer_deleting")
                return existing, False
            computer = Computer(
                owner_id=self.owner_id,
                agent_id=agent.id,
                provider="docker",
                kind=kind,
                image=image,
                workspace_path=workspace,
                state="missing",
                **values,
            )
            db.add(computer)
            await db.commit()
            await db.refresh(computer)
            return computer, True

    async def update_config(self, computer_id: int, **changes) -> Computer:
        """Change settings. Limits apply to a live machine at once; the image
        and network apply on the next recreate (the status says so)."""
        async with self._lock(computer_id):
            computer = await self.get(computer_id)
            values = _checked_config({k: v for k, v in changes.items() if k != "image"}, partial=True)
            if "image" in changes and changes["image"] is not None:
                values["image"] = docker_cli.check_image(changes["image"])
            computer = await self._save(computer_id, **values)
            if any(k in values for k in ("cpu_millis", "memory_mb", "pids_limit")) and computer.state in ("running", "stopped"):
                try:
                    await self._call(self.provider(computer.provider).update_limits, self.spec(computer))
                except ComputerError as exc:
                    computer = await self._save(computer_id, last_error=str(exc))
            return computer

    # -- lifecycle ---------------------------------------------------------------------

    async def _require_docker(self) -> None:
        health = await self.health(fresh=True)
        if not health.available:
            raise ComputerError(health.error or "Docker is not available.", "docker_unavailable")

    async def prepare_image(self, computer_id: int) -> Computer:
        """Download the computer's image (the only step that does)."""
        async with self._lock(computer_id):
            computer = await self.get(computer_id)
            await self._require_docker()
            provider = self.provider(computer.provider)
            if computer.kind == "desktop" and computer.image == docker_cli.DESKTOP_IMAGE:
                raise ComputerError("Build the desktop image locally first: docker build -t open-dots-desktop:local desktop-image. See docs/DESKTOP-IMAGE.md.", "image_missing")
            await self._call(provider.pull, computer.image)
            return await self._save(computer_id, last_error=None)

    async def start(self, computer_id: int) -> Computer:
        """Make the machine if it is missing, then start it."""
        async with self._lock(computer_id):
            return await self._start_locked(await self.get(computer_id))

    async def _start_locked(self, computer: Computer) -> Computer:
        await self._require_docker()
        provider = self.provider(computer.provider)
        if computer.workspace_path:
            Path(computer.workspace_path).mkdir(parents=True, exist_ok=True)
        try:
            await self._save(computer.id, state="creating" if computer.state == "missing" else computer.state)
            status = await self._call(provider.create, self.spec(computer))
            if computer.kind == "desktop":
                deadline = time.monotonic() + 60
                while status.state == "running" and not status.desktop_ready and time.monotonic() < deadline:
                    await asyncio.sleep(1)
                    status = await self._call(provider.status, self.spec(computer))
                if not status.desktop_ready:
                    raise ComputerError("The desktop did not come up in 60 seconds. Check the container logs.", "desktop_not_ready")
        except ComputerError as exc:
            await self._save(computer.id, state="error", last_error=str(exc))
            raise
        image_id = status.image_id or computer.image_id
        return await self._apply_status(computer.id, status, image_id=image_id, last_error=None)

    async def stop(self, computer_id: int) -> Computer:
        async with self._lock(computer_id):
            computer = await self.get(computer_id)
            if self._leases.get(computer_id):
                raise ComputerError("A turn is using this computer. Stop the turn first.", "computer_busy")
            self.release_control(computer_id)
            if hasattr(self, "viewers"): await self.viewers.close_computer(computer_id)
            self._capture_cache.pop(computer_id, None)
            await self._require_docker()
            status = await self._call(self.provider(computer.provider).stop, self.spec(computer))
            return await self._apply_status(computer_id, status)

    async def recreate(self, computer_id: int, *, workspace: str | None = None) -> Computer:
        """Remove the machine and make it again from the current settings. The
        workspace folder is kept; anything else inside the machine is lost."""
        async with self._lock(computer_id):
            computer = await self.get(computer_id)
            if self._leases.get(computer_id):
                raise ComputerError("A turn is using this computer. Stop the turn first.", "computer_busy")
            await self._require_docker()
            self.release_control(computer.id)
            if hasattr(self, "viewers"): await self.viewers.close_computer(computer.id)
            self._capture_cache.pop(computer.id, None)
            await self._call(self.provider(computer.provider).delete, self.spec(computer))
            values = {"state": "missing", "container_id": None, "image_id": None}
            if workspace:
                values["workspace_path"] = workspace
            computer = await self._save(computer_id, **values)
            return await self._start_locked(computer)

    async def delete(self, computer_id: int) -> bool:
        """Remove the machine, then the row. True when both are gone; False when
        the machine could not be removed now (the row stays `deleting` and
        upkeep finishes it). Workspace files are never touched."""
        async with self._lock(computer_id):
            computer = await self.get(computer_id)
            if self._leases.get(computer_id):
                raise ComputerError("A turn is using this computer. Stop the turn first.", "computer_busy")
            await self._save(computer_id, state="deleting")
            return await self._finish_delete(computer)

    async def mark_agent_deleted(self, agent_id: int) -> None:
        """The agent is being removed: its computer goes with it."""
        async with self.session() as db:
            computer = (
                await db.execute(select(Computer).where(Computer.agent_id == agent_id))
            ).scalar_one_or_none()
        if computer is None:
            return
        async with self._lock(computer.id):
            await self._save(computer.id, state="deleting")
            await self._finish_delete(computer)

    async def _finish_delete(self, computer: Computer) -> bool:
        try:
            health = await self.health(fresh=True)
            if not health.available:
                raise ComputerError(health.error or "Docker is not available.", "docker_unavailable")
            self.release_control(computer.id)
            if hasattr(self, "viewers"): await self.viewers.close_computer(computer.id)
            self._capture_cache.pop(computer.id, None)
            await self._call(self.provider(computer.provider).delete, self.spec(computer))
        except ComputerError as exc:
            await self._save(computer.id, last_error=f"Removal is waiting: {exc}")
            return False
        async with self.session() as db:
            await begin_write(db)
            await db.execute(delete(Computer).where(Computer.id == computer.id))
            await db.commit()
        shutil.rmtree(self.state_dir() / str(computer.id), ignore_errors=True)
        self._locks.pop(computer.id, None)
        return True

    async def _apply_status(self, computer_id: int, status: Status, **more) -> Computer:
        state = {"running": "running", "stopped": "stopped", "missing": "missing"}.get(status.state, "error")
        values = {"state": state, "container_id": status.container_id, **more}
        if state == "error":
            values["last_error"] = status.problem
        return await self._save(computer_id, **values)

    async def live_status(self, computer: Computer) -> tuple[Health, Status | None]:
        """What the provider sees now, and the row updated to match. With
        Docker down the status is None and the row is left as it was."""
        health = await self.health()
        if not health.available:
            return health, None
        try:
            status = await self._call(self.provider(computer.provider).status, self.spec(computer))
        except ComputerError as exc:
            return health, Status("unavailable", problem=str(exc))
        if computer.state not in ("creating", "deleting"):
            mapped = {"running": "running", "stopped": "stopped", "missing": "missing"}.get(status.state, "error")
            if mapped != computer.state or status.container_id != computer.container_id:
                try:
                    await self._apply_status(computer.id, status)
                except ComputerNotFound:
                    pass
        return health, status

    def last_command(self, computer_id: int) -> dict | None:
        path = self.state_dir() / str(computer_id) / LAST_COMMAND_FILE
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(record, dict):
            return None
        at = record.get("at")
        if isinstance(at, (int, float)):
            record["at"] = datetime.fromtimestamp(at, UTC).isoformat()
        return record

    def busy(self, computer_id: int) -> bool:
        return bool(self._leases.get(computer_id))

    # -- turns -------------------------------------------------------------------------

    async def attach_for_turn(self, agent_id: int | None, turn_id: int, thread_workspace: str | None) -> TurnComputer | None:
        """The computer this turn runs its tools in, started if needed, with a
        lease held until `release_turn`. None when the agent has no computer.
        Raises ComputerUnavailable when it has one that cannot be used."""
        if agent_id is None:
            return None
        from app.computers import native
        if await native.selected(agent_id):
            return await native.attach(agent_id, turn_id, self.state_dir())
        computer = await self.for_agent(agent_id)
        if computer is None:
            return None
        async with self._lock(computer.id):
            computer = await self.get(computer.id)
            if thread_workspace and computer.workspace_path and Path(thread_workspace) != Path(computer.workspace_path):
                raise ComputerUnavailable(
                    "This conversation's folder is not the one mounted in the agent's computer "
                    f"({computer.workspace_path}). Recreate the computer from the Agents panel, or start "
                    "a new conversation."
                )
            health = await self.health(fresh=True)
            if not health.available:
                raise ComputerUnavailable(f"The agent's computer cannot be reached: {health.error}")
            provider = self.provider(computer.provider)
            try:
                status = await self._call(provider.status, self.spec(computer))
            except ComputerError as exc:
                raise ComputerUnavailable(str(exc)) from None
            if status.state in ("foreign", "unsafe"):
                raise ComputerUnavailable(status.problem or "The agent's computer cannot be used.")
            if status.state != "running":
                if not computer.auto_start:
                    raise ComputerUnavailable(
                        "The agent's computer is not running and starting it automatically is off. "
                        "Start it from the Agents panel."
                    )
                if status.state == "missing" and not await self._call(provider.image_present, computer.image):
                    raise ComputerUnavailable(
                        f"The agent's computer has not been made yet and its image ({computer.image}) is not "
                        "on this machine. Prepare the image in the Agents panel first."
                    )
                try:
                    computer = await self._start_locked(computer)
                except ComputerError as exc:
                    raise ComputerUnavailable(f"The agent's computer could not be started: {exc}") from None
            self._leases.setdefault(computer.id, set()).add(turn_id)
            await self._save(computer.id, last_used_at=_utcnow())
        grants_dir = self.state_dir() / "grants" / f"turn-{turn_id}-{secrets.token_hex(6)}"
        spec = self.spec(computer)
        return TurnComputer(
            computer_id=computer.id,
            kind=computer.kind,
            name=spec.name,
            image=computer.image,
            provider=computer.provider,
            launch=provider.mcp_launch(spec, grants_dir, self.state_dir() / str(computer.id)),
            grants_dir=grants_dir,
        )

    async def release_turn(self, binding: TurnComputer | None, turn_id: int) -> None:
        if binding is None:
            return
        if binding.provider == "native":
            from app.computers import native
            await native.release(binding, turn_id)
            return
        held = self._leases.get(binding.computer_id)
        if held is not None:
            held.discard(turn_id)
            if not held:
                self._leases.pop(binding.computer_id, None)
        with screen.driver_lock(self.state_dir() / str(binding.computer_id)):
            owner_path = self.state_dir() / str(binding.computer_id) / "screen-owner.json"
            if screen.read_state(owner_path).get("turn_id") == str(binding.grants_dir):
                owner_path.unlink(missing_ok=True)
        shutil.rmtree(binding.grants_dir, ignore_errors=True)
        try:
            await self._save(binding.computer_id, last_used_at=_utcnow())
        except ComputerNotFound:
            pass

    # -- upkeep ------------------------------------------------------------------------

    def _last_activity(self, computer: Computer) -> datetime | None:
        times = [computer.last_used_at] if computer.last_used_at else []
        try:
            mtime = (self.state_dir() / str(computer.id) / LAST_COMMAND_FILE).stat().st_mtime
            times.append(datetime.fromtimestamp(mtime, UTC))
        except OSError:
            pass
        return max(times) if times else None

    async def upkeep(self) -> None:
        """Background job: finish interrupted removals, remove orphans once
        Docker answers, and stop computers that have been idle too long."""
        self.expire_control()
        health = await self.health(fresh=True)
        if not health.available:
            return
        async with self.session() as db:
            rows = list((await db.execute(select(Computer).where(Computer.owner_id == self.owner_id))).scalars())
        for computer in rows:
            if computer.state == "deleting":
                async with self._lock(computer.id):
                    await self._finish_delete(computer)
        if not self._orphans_checked:
            await self.remove_orphans()
            self._orphans_checked = True
        now = _utcnow()
        for computer in rows:
            if computer.state != "running" or computer.idle_minutes <= 0 or self.busy(computer.id) or self.held(computer.id):
                continue
            last = self._last_activity(computer) or computer.updated_at
            if last is not None and now - last < timedelta(minutes=computer.idle_minutes):
                continue
            lock = self._lock(computer.id)
            if lock.locked():
                continue
            async with lock:
                if self.busy(computer.id) or self.held(computer.id):
                    continue
                try:
                    status = await self._call(self.provider(computer.provider).stop, self.spec(computer))
                    await self._apply_status(computer.id, status)
                    logger.info("stopped an idle computer", computer_id=computer.id)
                except (ComputerError, ComputerNotFound):
                    logger.warning("could not stop an idle computer", computer_id=computer.id)

    async def remove_orphans(self) -> list[str]:
        """Remove machines this installation made that no computer row knows."""
        async with self.session() as db:
            known = {row for row in (await db.execute(select(Computer.id))).scalars()}
        removed: list[str] = []
        provider = self.provider()
        try:
            found = await self._call(provider.list_managed, self.instance())
        except ComputerError:
            return removed
        for name, computer_id in found:
            if computer_id is not None and computer_id in known:
                continue
            try:
                await self._call(provider.remove_by_name, name, self.instance())
                removed.append(name)
                logger.info("removed an orphaned computer", name=name)
            except ComputerError:
                logger.warning("could not remove an orphaned computer", name=name)
        return removed


_INT_FIELDS = {
    "memory_mb": (128, 262_144),
    "pids_limit": (32, 65_536),
    "idle_minutes": (0, 7 * 24 * 60),
}


def _checked_config(config: dict, *, partial: bool = False) -> dict:
    values: dict = {}
    for key, value in config.items():
        if value is None:
            continue
        if key == "cpus":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0.1 <= value <= 64):
                raise ValueError("cpus must be between 0.1 and 64")
            values["cpu_millis"] = int(round(value * 1000))
        elif key in _INT_FIELDS:
            low, high = _INT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
                raise ValueError(f"{key} must be between {low} and {high}")
            values[key] = value
        elif key == "network":
            if value not in docker_cli.NETWORK_MODES:
                raise ValueError(f"network must be one of: {', '.join(docker_cli.NETWORK_MODES)}")
            values["network"] = value
        elif key == "auto_start":
            values["auto_start"] = bool(value)
        else:
            raise ValueError(f"unknown computer setting: {key}")
    return values

