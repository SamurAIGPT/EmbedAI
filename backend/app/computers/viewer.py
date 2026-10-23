"""Single-use viewer tickets and an RFB bridge over Docker exec stdio."""
import asyncio
import os
import secrets
import signal
import time
from dataclasses import dataclass, field

from app.computers import docker_cli


@dataclass(repr=False)
class Ticket:
    computer_id: int
    lease: str
    identity: tuple
    until: float


@dataclass
class Viewers:
    tickets: dict[str, Ticket] = field(default_factory=dict, repr=False)
    active: dict[str, tuple[int, object]] = field(default_factory=dict, repr=False)

    def issue(self, computer_id, lease, identity):
        now = time.monotonic()
        self.tickets = {k: v for k, v in self.tickets.items() if v.until > now}
        # A signed-in client cannot fill unbounded memory by minting tickets.
        if len(self.tickets) >= 256:
            raise ValueError("Too many pending viewers. Wait 30 seconds.")
        token = secrets.token_hex(32)
        self.tickets[token] = Ticket(computer_id, lease, identity, now+30)
        return token

    def consume(self, token, computer_id, identity):
        ticket = self.tickets.pop(token, None)
        if not ticket or ticket.until <= time.monotonic() or ticket.computer_id != computer_id or ticket.identity != identity:
            return None
        return ticket

    def reserve(self, computer_id, websocket):
        if len(self.active) >= 8 or sum(cid == computer_id for cid, _ in self.active.values()) >= 3:
            return None
        ident = secrets.token_hex(16)
        self.active[ident] = (computer_id, websocket)
        return ident

    async def close_computer(self, computer_id):
        for _, (cid, ws) in list(self.active.items()):
            if cid == computer_id:
                try: await ws.close(code=4410)
                except Exception: pass


async def bridge(websocket, spec, docker, valid):
    """Bounded binary messages, backpressure, first-byte timeout and revalidation."""
    # No browser credentials or engine API keys reach the child process.
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "SSH_AUTH_SOCK"}}
    proc = await asyncio.create_subprocess_exec(
        docker or docker_cli.docker_executable() or "docker", *docker_cli.rfb_args(spec.name, spec.run_as),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env,
        **({"start_new_session": True} if os.name != "nt" else {}),
    )
    async def outbound():
        first = True
        while True:
            data = await asyncio.wait_for(proc.stdout.read(65536), 10 if first else 60)
            if not data: return
            first = False
            await websocket.send_bytes(data)
    async def inbound():
        while True:
            data = await websocket.receive_bytes()
            if len(data) > 65536:
                await websocket.close(code=1009); return
            proc.stdin.write(data)
            await proc.stdin.drain()
    async def revalidate():
        while True:
            await asyncio.sleep(5)
            if not await valid():
                await websocket.close(code=4409); return
    tasks = [asyncio.create_task(fn()) for fn in (outbound, inbound, revalidate)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if proc.stdin: proc.stdin.close()
        if proc.returncode is None:
            try:
                if os.name != "nt": os.killpg(proc.pid, signal.SIGKILL)
                else: proc.kill()
            except ProcessLookupError: pass
        await proc.wait()
