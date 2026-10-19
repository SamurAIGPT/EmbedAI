"""The page layer in front of the API (app/server/static_ui.py): what it answers,
what it leaves to the application, and the headers it sets."""

import httpx
import pytest
from starlette.responses import JSONResponse

from app.server.static_ui import serve_ui


async def inner_app(scope, receive, send):
    """Stands in for the API: says what reached it."""
    if scope["type"] != "http":
        return
    response = JSONResponse({"inner": True, "method": scope["method"], "path": scope["path"]}, status_code=401)
    await response(scope, receive, send)


@pytest.fixture
def ui(tmp_path):
    (tmp_path / "index.html").write_text("<html>home</html>")
    (tmp_path / "404.html").write_text("<html>missing</html>")
    (tmp_path / "login").mkdir()
    (tmp_path / "login" / "index.html").write_text("<html>login</html>")
    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "_next" / "static" / "app.js").write_text("console.log(1)")
    return tmp_path


def client(ui_dir) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(serve_ui(inner_app, ui_dir))
    return httpx.AsyncClient(transport=transport, base_url="http://dots.example.com")


async def test_pages_load_without_a_session(ui):
    async with client(ui) as api:
        for path, text in (("/", "home"), ("/login/", "login")):
            response = await api.get(path)
            assert response.status_code == 200 and text in response.text


async def test_head_is_a_page_request(ui):
    async with client(ui) as api:
        assert (await api.head("/login/")).status_code == 200


async def test_the_api_and_webhooks_go_to_the_application(ui):
    async with client(ui) as api:
        for path in ("/api/v1/agents", "/api/v1/health", "/api/", "/api", "/hooks/7", "/docs", "/openapi.json", "/redoc"):
            response = await api.get(path)
            assert response.status_code == 401 and response.json()["inner"] is True, path


async def test_other_methods_go_to_the_application_even_on_a_page_path(ui):
    async with client(ui) as api:
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            response = await api.request(method, "/login/")
            assert response.status_code == 401 and response.json()["method"] == method


async def test_a_path_that_only_starts_like_the_api_is_a_page_request(ui):
    async with client(ui) as api:
        response = await api.get("/apiary")
        assert response.status_code == 404 and "missing" in response.text


async def test_a_missing_page_is_a_404_not_a_sign_in_request(ui):
    async with client(ui) as api:
        response = await api.get("/nope/")
        assert response.status_code == 404 and "missing" in response.text


async def test_a_missing_page_is_a_404_even_without_a_404_page_in_the_build(ui):
    (ui / "404.html").unlink()
    async with client(ui) as api:
        response = await api.get("/nope/")
        assert response.status_code == 404
        assert response.headers["x-content-type-options"] == "nosniff"


async def test_the_redirect_to_the_folder_form_is_relative_and_keeps_the_query(ui):
    async with client(ui) as api:
        response = await api.get("/login?next=%2Fchat", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/login/?next=%2Fchat"


async def test_a_page_cannot_be_framed_and_is_not_cached(ui):
    async with client(ui) as api:
        headers = (await api.get("/login/")).headers
        assert headers["content-security-policy"] == "frame-ancestors 'none'"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cache-control"] == "no-cache"
        assert headers["x-content-type-options"] == "nosniff"


async def test_hashed_assets_are_cached_for_good(ui):
    async with client(ui) as api:
        response = await api.get("/_next/static/app.js")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert "frame-ancestors" not in response.headers.get("content-security-policy", "")


async def test_a_path_outside_the_folder_is_never_served(ui, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "loot.txt").write_text("loot")
    app = serve_ui(inner_app, ui)
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    for path in ("/../loot.txt", "/..%2floot.txt", "/login/../../loot.txt"):
        sent.clear()
        scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "headers": [], "scheme": "http", "server": ("x", 80), "client": ("1.2.3.4", 1), "root_path": ""}
        await app(scope, receive, send)
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert b"loot" not in body, path


async def test_other_scope_types_pass_through(ui):
    seen = []

    async def lifespan_app(scope, receive, send):
        seen.append(scope["type"])

    app = serve_ui(lifespan_app, ui)
    await app({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


def test_a_missing_folder_is_an_error(tmp_path):
    with pytest.raises(RuntimeError):
        serve_ui(inner_app, tmp_path / "nowhere")


async def test_in_front_of_the_real_application_pages_load_and_the_api_still_asks_for_sign_in(ui, monkeypatch):
    """The point of the layer: /login loads with no session, the API does not."""
    from app.core.config import settings
    from app.main import app as real_app

    monkeypatch.setattr(settings, "TRUSTED_HOSTS", "localhost,127.0.0.1,[::1],dots.example.com")
    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "off")
    transport = httpx.ASGITransport(serve_ui(real_app, ui), client=("203.0.113.7", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://dots.example.com") as api:
        assert (await api.get("/login/")).status_code == 200
        assert (await api.get("/api/v1/health")).json() == {"status": "ok"}
        refused = await api.get("/api/v1/agents")
        assert refused.status_code == 401 and refused.json()["detail"]["error_code"] == "auth_required"
        assert (await api.post("/login/", json={})).status_code == 401  # not a page request
