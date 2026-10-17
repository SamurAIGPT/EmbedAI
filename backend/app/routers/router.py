from app.routers.sandbox import router as sandbox_router
from app.routers.task import router as task_router
from app.routers.link import router as link_router
from app.routers.schedule import router as schedule_router
from app.routers.permission import router as permissions_router
from app.routers.local import router as local_router
from app.routers.providers import router as providers_router
from app.routers.agents import router as agents_router
from app.routers.threads import router as threads_router
from app.routers.settings import router as settings_router
from app.routers.decisions import router as decisions_router
from app.routers.approval_rules import router as approval_rules_router
from app.routers.sentinel import router as sentinel_router
from app.routers.computers import router as computers_router
from app.routers.engines import router as engines_router
from app.routers.routines import router as routines_router
from app.routers.search import router as search_router
from app.routers.attachments import router as attachments_router
from app.routers.workspace_files import router as workspace_files_router
from app.routers.usage import router as usage_router
from app.auth.routes import router as auth_router
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health():
    """Public: says the server is up and nothing else."""
    return {"status": "ok"}


router.include_router(auth_router)
router.include_router(sandbox_router)
router.include_router(task_router)
router.include_router(link_router)
router.include_router(permissions_router)
router.include_router(schedule_router)
router.include_router(local_router)
router.include_router(providers_router)
router.include_router(agents_router)
router.include_router(threads_router)
router.include_router(settings_router)
router.include_router(decisions_router)
router.include_router(approval_rules_router)
router.include_router(sentinel_router)
router.include_router(computers_router)
router.include_router(engines_router)
router.include_router(routines_router)
router.include_router(search_router)
router.include_router(attachments_router)
router.include_router(workspace_files_router)
router.include_router(usage_router)
