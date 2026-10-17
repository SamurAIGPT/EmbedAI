"""Engines: which agent engines this app can run conversations on (Claude,
Codex, an ACP agent, an OpenAI-compatible endpoint), whether each is usable
now, its models and what it can do (docs/ENGINES.md).

Status checks run the engine's CLI or reach its endpoint, so the local access
check covers the whole router, as for threads and agents. A check that fails
or hangs reports the engine as unavailable with the reason; it never fails
the request.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.engine import EngineConfigRequest, EngineResponse
from app.services import engine_config
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import ThreadRuntime
from app.services.thread_runtime import runtime as default_runtime

router = APIRouter(
    prefix="/engines",
    tags=["Engines"],
    dependencies=[Depends(require_local_access)],
)


def get_runtime() -> ThreadRuntime:
    return default_runtime


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error_code": code, "message": message})


def _configurable(rt: ThreadRuntime, engine_id: str):
    adapter = rt.adapters.get(engine_id)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Engine not found")
    if not getattr(adapter, "needs_config", False) or not callable(getattr(adapter, "validate_config", None)):
        raise _error(409, "not_configurable", "This engine has nothing to configure here.")
    return adapter


@router.get("", response_model=list[EngineResponse])
async def list_engines(refresh: bool = False, rt: ThreadRuntime = Depends(get_runtime)):
    """Every engine, with status, models and capabilities. Results are kept
    for a few seconds; `refresh=true` checks again now."""
    return await rt.engines.describe(refresh=refresh)


@router.get("/{engine_id}", response_model=EngineResponse)
async def get_engine(engine_id: str, refresh: bool = False, rt: ThreadRuntime = Depends(get_runtime)):
    if engine_id not in rt.adapters:
        raise HTTPException(status_code=404, detail="Engine not found")
    return (await rt.engines.describe(refresh=refresh, only=engine_id))[0]


@router.put("/{engine_id}/config", response_model=EngineResponse)
async def save_engine_config(
    engine_id: str, payload: EngineConfigRequest, rt: ThreadRuntime = Depends(get_runtime)
):
    """Save the engine's configuration (encrypted, like every credential) and
    check the engine again. The response never includes a key."""
    adapter = _configurable(rt, engine_id)
    current = await rt.engine_config(engine_id)
    try:
        config = adapter.validate_config(payload.model_dump(exclude_none=True), current)
    except ValueError as exc:
        raise _error(422, "invalid_engine_config", str(exc)) from None
    async with rt.session() as db:
        await engine_config.save(db, rt.owner_id, engine_id, config)
    rt.engines.forget(engine_id)
    return (await rt.engines.describe(refresh=True, only=engine_id))[0]


@router.delete("/{engine_id}/config", status_code=204)
async def delete_engine_config(engine_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """Forget the engine's configuration, key included."""
    _configurable(rt, engine_id)
    async with rt.session() as db:
        await engine_config.remove(db, rt.owner_id, engine_id)
    rt.engines.forget(engine_id)
