"""The bundled template catalog and its read-only routes."""

import re

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.templates import router as templates_router
from app.schemas.agent import AgentCreateRequest
from app.templates import catalog

# The catalog is public text: it must not name any other product.
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


def client(host=("127.0.0.1", 50000)):
    app = FastAPI()
    app.include_router(templates_router, prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=host), base_url="http://localhost")


def test_ids_are_unique_and_stable_looking():
    ids = [t.id for t in catalog.TEMPLATES]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z]+(-[a-z]+)+", i) for i in ids)
    assert len(ids) >= 12


def test_every_template_is_complete_and_bounded():
    for t in catalog.TEMPLATES:
        assert t.category in catalog.CATEGORIES, t.id
        assert t.id.startswith(t.category + "-"), t.id
        assert 0 < len(t.instructions) <= catalog.INSTRUCTIONS_MAX, t.id
        assert t.name and t.role and t.description and t.example_requests, t.id
        assert re.fullmatch(r"#[0-9a-f]{6}", t.color), t.id
        assert len(t.name) <= 80 and len(t.role) <= 200 and len(t.description) <= 4000
        assert len(t.starter_notes or "") <= catalog.NOTES_MAX, t.id
        for skill in t.skills:
            assert SKILL_NAME.match(skill.name) and len(skill.name) <= 64, (t.id, skill.name)
            assert 0 < len(skill.body) <= catalog.SKILL_BODY_MAX
            assert skill.description


def test_a_template_fills_a_valid_agent_request():
    for t in catalog.TEMPLATES:
        AgentCreateRequest(name=t.name, title=t.role, description=t.description, instructions=t.instructions, starter_notes=t.starter_notes)


def test_every_category_has_a_template_and_most_roles_need_no_connection():
    assert {t.category for t in catalog.TEMPLATES} == set(catalog.CATEGORIES)
    assert sum(1 for t in catalog.TEMPLATES if t.available) > len(catalog.TEMPLATES) // 2


def test_a_missing_connection_makes_a_template_unavailable():
    assert not catalog.get("work-mailroom").available
    assert catalog.get("everyday-helper").available

