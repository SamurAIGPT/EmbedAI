import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.background import run_every
from app.core.config import settings
from app.core import app_settings, data_lease, engine_processes, scheduler_lock, shutdown
from app.desktop import profile
from app.core.logging import configure_logging, get_logger
from app.auth.middleware import AuthMiddleware
from app.desktop.launch_auth import LaunchAuthMiddleware
from app.routers.router import router as app_router
from app.routers.hooks import router as hooks_router
from app.services import routine_service, schedule_service, task_execution_service
from app.services.thread_runtime import runtime as thread_runtime
from app.services.box_operations import BoatNotConfigured

BACKGROUND_INTERVAL_SECONDS = 30
# How often the lease sweep looks for turns whose owner stopped. Much shorter
# than THREAD_LEASE_SECONDS, so a dead turn is noticed soon after its lease ends.
THREAD_SWEEP_INTERVAL_SECONDS = 15


configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One backend per SQLite database: a second one refuses to start here,
    # before it can run a routine or sweep the first one's turns.
    try:
        lease = data_lease.acquire_for_settings(settings)
    except data_lease.DataDirInUse as exc:
        logger.error(str(exc))
        raise
    try:
        async with _serving(app):
            yield
    finally:
        if lease is not None:
            lease.release()


@asynccontextmanager
async def _serving(app: FastAPI):
    # Event streams end as soon as a stop signal arrives, so the server's wait
    # for open connections never holds the shutdown below up (app/core/shutdown.py).
    shutdown.reset()
    shutdown.install_signal_hooks()
    thread_runtime.reopen()
    if profile.active():
        from app.db.session import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as db:
                await app_settings.refresh(db)
        except Exception:  # noqa: BLE001
            # A damaged settings row must never stop startup (D-013 4.4a);
            # the resolver then serves the profile defaults.
            logger.exception("could not load saved settings; using defaults")
    logger.info(f"Database in use: {settings.DATABASE_DESCRIPTION}")
    # Engine processes a hard kill of an earlier backend left running (they
    # run in sessions of their own) are ended now; only ones verifiably ours
    # (app/core/engine_processes.py).
    await asyncio.to_thread(engine_processes.sweep)
    # One routine scheduler per database: on PostgreSQL only the backend that
    # holds the advisory lock runs it; the others stand by and take over when
    # it stops (app/core/scheduler_lock.py). SQLite's file lease already
    # means one backend.
    guard = scheduler_lock.SchedulerLock(settings.DATABASE_KIND)
    routines = _GuardedRoutines(app, guard)
    await routines.ensure()
    jobs = [
        asyncio.create_task(
            run_every(BACKGROUND_INTERVAL_SECONDS, schedule_service.poll_due_schedules)
        ),
        asyncio.create_task(
            run_every(BACKGROUND_INTERVAL_SECONDS, task_execution_service.sync_active_tasks)
        ),
        asyncio.create_task(
            run_every(BACKGROUND_INTERVAL_SECONDS, schedule_service.report_finished_runs)
        ),
        # Runs once at startup, then periodically.
        asyncio.create_task(run_every(THREAD_SWEEP_INTERVAL_SECONDS, thread_runtime.sweep_stale)),
        # Agent computers: finish interrupted removals, remove orphans once
        # Docker answers, stop idle ones. Does nothing while Docker is down.
        asyncio.create_task(run_every(BACKGROUND_INTERVAL_SECONDS, thread_runtime.computers.upkeep)),
        # The nightly memory tidy-up for agents that are due and idle (memory_capture.py).
        asyncio.create_task(run_every(600, thread_runtime.capture.tick)),
        # Local routines: due occurrences, starts, approvals, run limits, endings.
        asyncio.create_task(run_every(settings.ROUTINE_POLL_SECONDS, routines.tick)),
    ]
    yield
    # Stopping (SIGTERM, Ctrl+C, the desktop launcher going away). First the
    # background jobs, so no routine starts a new turn while we stop.
    for job in jobs:
        job.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)
    # Kill this process's provider processes (and their MCP servers, which
    # share their process group) and interrupt its own turns now, rather than
    # leaving them for the lease to expire.
    await thread_runtime.shutdown()
    # Routine runs whose turns were just interrupted end interrupted now, with
    # their notification, instead of at the next start. Only the scheduler's
    # holder settles runs; then it lets another backend take over.
    if guard.held:
        await routine_service.scheduler.shutdown()
    await guard.release()


class _GuardedRoutines:
    """Runs the routine scheduler only while this process holds the scheduler
    lock. The first time it gets the lock (at startup, or on taking over from a
    backend that stopped) it first settles the runs the previous holder left
    running, so none of them is ever started again."""

    def __init__(self, app: FastAPI, guard: scheduler_lock.SchedulerLock) -> None:
        self.app = app
        self.guard = guard
        self.recovered = False

    async def ensure(self) -> bool:
        held = await self.guard.ensure()
        if held and not self.recovered:
            self.recovered = True
            try:
                await routine_service.scheduler.recover()
            except Exception:  # noqa: BLE001
                logger.exception("could not settle routine runs left by the previous process")
        elif not held:
            # Recover again when the lock comes back: runs may have been left
            # by the backend that held it meanwhile.
            self.recovered = False
        routine_service.scheduler.enabled = held
        self.app.state.routines_run_here = held
        return held

    async def tick(self) -> None:
        if await self.ensure():
            await routine_service.scheduler.tick()
            from app.services.thread_runtime import runtime
            await runtime.rooms.tick()


app = FastAPI(lifespan=lifespan)

# Owner sign-in, paired devices, Host/Origin allowlist and CSRF (docs/AUTH.md).
# Innermost: under the desktop profile LaunchAuthMiddleware has already demanded
# the launch token, and this one then steps aside.
app.add_middleware(AuthMiddleware)

# Desktop launches only (a no-op otherwise). Added before CORS, which makes it the
# inner of the two: a preflight is answered by CORS without a token, and a refusal
# still carries the CORS headers the page needs to read it.
app.add_middleware(LaunchAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGIN_LIST,
    # The web UI's session is a cookie, sent cross-port in development.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(BoatNotConfigured)
async def boat_not_configured_handler(request: Request, exc: BoatNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(app_router)
# Webhook deliveries for routines, outside /api/v1: authenticated by each
# routine's own secret (also served alone by app/ingress.py).
app.include_router(hooks_router)
