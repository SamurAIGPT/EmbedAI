import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.background import run_every
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.desktop.launch_auth import LaunchAuthMiddleware
from app.routers.router import router as app_router
from app.services import schedule_service, task_execution_service
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
    logger.info(f"Database in use: {settings.DATABASE_DESCRIPTION}")
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
    ]
    yield
    # Kill this process's provider processes and interrupt its own turns now,
    # rather than leaving them for the lease to expire.
    await thread_runtime.shutdown()
    for job in jobs:
        job.cancel()
    await asyncio.gather(*jobs, return_exceptions=True)


app = FastAPI(lifespan=lifespan)

# Desktop launches only (a no-op otherwise). Added before CORS, which makes it the
# inner of the two: a preflight is answered by CORS without a token, and a refusal
# still carries the CORS headers the page needs to read it.
app.add_middleware(LaunchAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.FRONTEND_ORIGIN_LIST,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(BoatNotConfigured)
async def boat_not_configured_handler(request: Request, exc: BoatNotConfigured):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(app_router)
