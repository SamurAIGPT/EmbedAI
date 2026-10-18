"""Boat is optional: the app configures, starts and refuses Boat-only work
cleanly when BOAT_API_KEY (and the permission-hook settings) are empty."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app
from app.services.box_operations import BoatNotConfigured, ascii_box_service


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("BOAT_API_KEY", "HOOK_TOKEN", "PERMISSION_HOOK_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_settings_load_without_any_boat_values():
    s = Settings(_env_file=None, TOKEN_ENCRYPTION_KEYS="x")
    assert s.BOAT_ENABLED is False
    assert s.HOOK_TOKEN == "" and s.PERMISSION_HOOK_BASE_URL == ""


def test_boat_is_enabled_only_by_a_non_blank_key():
    assert Settings(_env_file=None, TOKEN_ENCRYPTION_KEYS="x", BOAT_API_KEY="k").BOAT_ENABLED
    assert not Settings(_env_file=None, TOKEN_ENCRYPTION_KEYS="x", BOAT_API_KEY="  ").BOAT_ENABLED


def test_box_service_refuses_clearly_without_a_key(monkeypatch):
    monkeypatch.setattr(settings, "BOAT_API_KEY", "")
    with pytest.raises(BoatNotConfigured):
        ascii_box_service.get_box("any")


def test_hook_endpoint_rejects_the_empty_bearer_when_no_token_is_set(monkeypatch):
    """With HOOK_TOKEN empty, 'Bearer ' must not authenticate."""
    monkeypatch.setattr(settings, "HOOK_TOKEN", "")
    client = TestClient(app, base_url="http://localhost")
    for header in ("Bearer ", "Bearer", ""):
        r = client.post("/api/v1/permissions/check/u/1", headers={"Authorization": header}, json={})
        assert r.status_code == 401
