from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


async def get_providers():
    async with AsyncClient(transport=ASGITransport(app), base_url="http://owned.test") as client:
        return await client.get("/api/v1/providers")


async def test_boat_is_reported_on_when_a_key_is_set(monkeypatch):
    monkeypatch.setattr(settings, "BOAT_API_KEY", "a-key")
    response = await get_providers()
    assert response.status_code == 200
    assert response.json() == {"boat": {"enabled": True, "reason": None}}


async def test_boat_is_reported_off_with_a_reason_when_the_key_is_empty(monkeypatch):
    monkeypatch.setattr(settings, "BOAT_API_KEY", "")
    body = (await get_providers()).json()
    assert body["boat"]["enabled"] is False
    assert "BOAT_API_KEY" in body["boat"]["reason"]


async def test_the_answer_never_contains_the_key_or_the_url(monkeypatch):
    monkeypatch.setattr(settings, "BOAT_API_KEY", "super-secret-key")
    monkeypatch.setattr(settings, "BOAT_BASE_URL", "https://internal.example/api")
    text = (await get_providers()).text
    assert "super-secret-key" not in text and "internal.example" not in text
