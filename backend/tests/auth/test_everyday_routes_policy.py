"""Who may use search, attachments, workspace files and usage
(docs/EVERYDAY.md, docs/AUTH.md)."""

import pytest

from app.auth.policy import is_public, required_scope
from tests.auth.test_auth_api import (  # noqa: F401
    REMOTE,
    app,
    auth_settings,
    client,
    error_code,
    factory,
    login,
    pairing_code,
    set_owner,
)

ATTACHMENT = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/search"),
        ("POST", "/api/v1/threads/3/attachments"),
        ("GET", f"/api/v1/threads/3/attachments/{ATTACHMENT}"),
    ],
)
def test_chatting_routes_are_in_the_chat_scope(method, path):
    assert required_scope(method, path) == "chat"
    assert not is_public(method, path)


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/threads/3/files"),
        ("GET", "/api/v1/threads/3/files/content"),
        ("GET", "/api/v1/threads/3/files/raw"),
        ("GET", "/api/v1/threads/3/files/download"),
        ("GET", "/api/v1/threads/3/files/zip"),
        ("GET", "/api/v1/usage"),
        ("GET", "/api/v1/usage.csv"),
        ("GET", "/api/v1/threads/3/attachments/../../files"),
        ("DELETE", f"/api/v1/threads/3/attachments/{ATTACHMENT}"),
    ],
)
def test_files_and_usage_are_admin_only(method, path):
    assert required_scope(method, path) == "admin"
    assert not is_public(method, path)


async def test_without_a_session_nothing_is_served(app, factory):
    async with client(app) as stranger:
        for path in (
            "/api/v1/threads/1/files/download?path=README.md",
            "/api/v1/search?q=hello",
            f"/api/v1/threads/1/attachments/{ATTACHMENT}",
            "/api/v1/usage",
        ):
            response = await stranger.get(path)
            assert response.status_code == 401, path


async def test_an_upload_from_a_signed_in_browser_needs_the_csrf_header(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        await login(owner)
        response = await owner.post("/api/v1/threads/1/attachments", files={"file": ("a.txt", b"x", "text/plain")})
        assert response.status_code == 403 and error_code(response) == "csrf_failed"


async def test_a_chat_device_cannot_browse_files_or_usage(app, factory):
    await set_owner(factory)
    async with client(app) as owner:
        code = await pairing_code(owner, (await login(owner))["csrf_token"], scope="chat")
    async with client(app, peer=("198.51.100.40", 1)) as phone:
        token = (await phone.post("/api/v1/auth/pair", json={"code": code})).json()["token"]
        bearer = {"Authorization": f"Bearer {token}"}
        for path in (
            "/api/v1/threads/1/files",
            "/api/v1/threads/1/files/download?path=a",
            "/api/v1/threads/1/files/zip",
            "/api/v1/usage",
        ):
            denied = await phone.get(path, headers=bearer)
            assert denied.status_code == 403 and error_code(denied) == "scope_denied", path
