"""Delete an agent and everything that goes with it, the same way for the
person's delete button and for an approved lead proposal."""
from app.services import agent_memory, agent_store as store


async def remove_agent(rt, agent_id: int) -> None:
    """Raises agent_store.AgentNotFound. Conversations stay, unlinked; notes and
    the agent's computer go; the project folder never does."""
    async with rt.session() as db:
        await store.delete_agent(db, agent_id)
    rt.capture.drop_agent(agent_id)
    agent_memory.remove_memory(agent_id)
    # If Docker is not answering, the computer's removal finishes later.
    try:
        await rt.computers.mark_agent_deleted(agent_id)
    except Exception:  # noqa: BLE001
        pass
