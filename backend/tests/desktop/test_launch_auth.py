"""Launcher-issued authentication: with the desktop profile on, nothing answers
without the launch token, and nothing changes when it is off (D-012 decision 5)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, StreamingResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from app.desktop import launch_auth, profile
from app.main import app

BACKEND_DIR = Path(__file__).parents[2]

TOKEN = "t" * 43
KEY = "k" * 43 + "="
HEADERS = {launch_auth.HEADER: TOKEN}


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setattr(
        profile, "_current",
        profile.DesktopProfile(data_dir=tmp_path, origin="http://127.0.0.1:1", encryption_key=KEY),
    )
    monkeypatch.setattr(launch_auth, "_token", TOKEN.encode())


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def every_route() -> list[tuple[str, str]]:
    """Every method and path of the real application. FastAPI includes routers
    lazily, so the API routes are read from the OpenAPI schema (which lists all of
    them: see the test below about routes that could hide) and the framework's own
    routes (docs, schema) from the route table."""
    found = []
    for path, operations in app.openapi()["paths"].items():
        found += [(method.upper(), concrete(path)) for method in operations if method != "parameters"]
    for route in app.routes:
        if isinstance(route, Route):
            found += [(m, concrete(route.path)) for m in sorted((route.methods or {"GET"}) - {"HEAD"})]
        elif isinstance(route, Mount):
            found.append(("GET", concrete(route.path) or "/"))
    return sorted(set(found))


ROUTES = every_route()


def test_the_route_walk_sees_the_whole_application():
    # Not a number to maintain: it fails only if the walk stops finding routes at all.
    assert len(ROUTES) > 40
    assert ("POST", "/api/v1/threads/1/stop") in ROUTES
    assert ("DELETE", "/api/v1/threads/1") in ROUTES
    assert ("GET", "/docs") in ROUTES


def test_no_route_hides_from_the_walk():
    """The walk reads the OpenAPI schema, which leaves out a route declared with
    include_in_schema=False, and the app has no WebSocket routes. If either appears
    this test fails, so the route walk above is extended before it can miss one."""
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (BACKEND_DIR / "app").rglob("*.py"))
    assert "include_in_schema" not in sources
    assert "websocket" not in sources.replace("websocket.close", "").replace('"websocket"', "")


@pytest.mark.parametrize("method, path", ROUTES)
def test_no_route_answers_without_the_token(desktop, client, method, path):
    response = client.request(method, path)
    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["detail"]["error_code"] == "launch_token_required"


@pytest.mark.parametrize("method, path", ROUTES)
def test_no_route_answers_to_a_wrong_token(desktop, client, method, path):
    response = client.request(method, path, headers={launch_auth.HEADER: "x" * 43})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/", "/api", "/api/v1", "/api/v1/nothing-here", "/chat", "/_next/static/a.js"])
def test_a_path_that_does_not_exist_is_refused_the_same_way(desktop, client, path):
    """No 404 to tell an unauthenticated caller which paths exist."""
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("method, path", [("GET", "/api/v1/providers"), ("GET", "/api/v1/threads"), ("POST", "/api/v1/threads/1/stop")])
def test_with_the_token_the_request_reaches_the_application(desktop, client, method, path):
    response = client.request(method, path, headers=HEADERS)
    assert response.status_code != 401
    if path == "/api/v1/providers":
        assert response.status_code == 200


def test_the_token_is_read_from_the_header_and_nowhere_else(desktop, client):
    assert client.get("/api/v1/providers", params={"token": TOKEN}).status_code == 401
    assert client.get("/api/v1/providers", params={launch_auth.HEADER: TOKEN}).status_code == 401
    assert client.get("/api/v1/providers", cookies={launch_auth.HEADER: TOKEN}).status_code == 401
    assert client.get("/api/v1/providers", headers={"authorization": f"Bearer {TOKEN}"}).status_code == 401


def test_a_repeated_token_header_is_refused(desktop, client):
    response = client.get("/api/v1/providers", headers=[(launch_auth.HEADER, TOKEN), (launch_auth.HEADER, TOKEN)])
    assert response.status_code == 401


def test_a_token_that_is_a_prefix_or_extension_is_refused(desktop, client):
    assert client.get("/api/v1/providers", headers={launch_auth.HEADER: TOKEN[:-1]}).status_code == 401
    assert client.get("/api/v1/providers", headers={launch_auth.HEADER: TOKEN + "t"}).status_code == 401
    assert client.get("/api/v1/providers", headers={launch_auth.HEADER: ""}).status_code == 401


def test_the_refusal_says_nothing_about_the_token_or_the_route(desktop, client):
    body = client.get("/api/v1/threads", headers={launch_auth.HEADER: "x" * 43}).text
    assert TOKEN not in body
    assert "threads" not in body


PAGE_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"  # what a browser sends to navigate


def test_a_browser_tab_gets_a_readable_page_and_the_apps_own_requests_get_json(desktop, client):
    tab = client.get("/", headers={"accept": PAGE_ACCEPT})
    assert tab.status_code == 401
    assert tab.headers["content-type"].startswith("text/html")
    assert "Open this from the Open Duet app" in tab.text
    assert TOKEN not in tab.text

    for accept in ("application/json", "*/*", "text/event-stream"):
        response = client.get("/api/v1/threads", headers={"accept": accept})
        assert response.status_code == 401
        assert response.json()["detail"]["error_code"] == "launch_token_required"
    # Only a navigation is shown a page: a request that changes something never is.
    assert client.post("/api/v1/threads", headers={"accept": PAGE_ACCEPT}).json()["detail"]["error_code"] == (
        "launch_token_required"
    )


def test_the_profile_on_without_a_token_refuses_everything_with_503(monkeypatch, desktop, client):
    """Never a silent fall back to unprotected: no token installed means closed."""
    monkeypatch.setattr(launch_auth, "_token", None)
    for headers in ({}, HEADERS, {"accept": PAGE_ACCEPT}):
        response = client.get("/api/v1/providers", headers=headers)
        assert response.status_code == 503
        assert response.json()["detail"]["error_code"] == "launch_token_unavailable"


def test_a_preflight_is_answered_without_a_token_and_a_refusal_is_readable_by_the_page(desktop, client):
    origin = "http://localhost:3000"  # what FRONTEND_ORIGINS holds in the test run
    preflight = client.options(
        "/api/v1/threads",
        headers={
            "origin": origin,
            "access-control-request-method": "POST",
            "access-control-request-headers": launch_auth.HEADER,
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    refused = client.get("/api/v1/threads", headers={"origin": origin})
    assert refused.status_code == 401
    assert refused.headers["access-control-allow-origin"] == origin


def test_with_the_profile_off_nothing_is_asked_for(monkeypatch):
    """Server and headless installs: the middleware is a no-op (owner
    authentication, docs/AUTH.md, decides instead; loopback trust here)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "LOOPBACK_TRUST", "programs")
    client = TestClient(app, raise_server_exceptions=False, base_url="http://localhost", client=("127.0.0.1", 50000))
    assert not profile.active()
    assert client.get("/api/v1/providers").status_code == 200
    unknown = client.get("/api/v1/nothing-here")
    assert unknown.status_code == 404  # the application's own answer, not the middleware's
    assert "launch_token" not in unknown.text


def wrapped(inner: Starlette):
    """A small app behind the real middleware, for what the real app lacks
    (static files, a stream, a WebSocket)."""
    return TestClient(launch_auth.LaunchAuthMiddleware(inner), raise_server_exceptions=False)


def test_static_files_need_the_token_too(desktop, tmp_path):
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("<h1>page</h1>", encoding="utf-8")
    client = wrapped(Starlette(routes=[Mount("/", StaticFiles(directory=tmp_path / "ui", html=True))]))
    assert client.get("/").status_code == 401
    assert client.get("/index.html").status_code == 401
    assert client.get("/missing.js").status_code == 401
    with_token = client.get("/", headers=HEADERS)
    assert (with_token.status_code, with_token.text) == (200, "<h1>page</h1>")


def test_a_stream_is_passed_through_chunk_by_chunk(desktop):
    def frames():
        for n in range(3):
            yield f"data: {n}\n\n"

    inner = Starlette(routes=[Route("/s", lambda request: StreamingResponse(frames(), media_type="text/event-stream"))])
    client = wrapped(inner)
    assert client.get("/s").status_code == 401
    got = client.get("/s", headers=HEADERS)
    assert got.status_code == 200
    assert got.text == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def test_a_websocket_without_the_token_is_refused_and_with_it_is_let_in(desktop):
    async def echo(websocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    client = wrapped(Starlette(routes=[WebSocketRoute("/ws", echo)]))
    with pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)):
        with client.websocket_connect("/ws"):
            pass
    with client.websocket_connect("/ws", headers=HEADERS) as socket:
        assert socket.receive_text() == "hello"


def test_the_ready_proof_depends_on_the_token_and_the_port(monkeypatch, desktop):
    first = launch_auth.proof_for_port(5000)
    assert first == launch_auth.proof_for_port(5000)
    assert first != launch_auth.proof_for_port(5001)
    monkeypatch.setattr(launch_auth, "_token", ("u" * 43).encode())
    assert launch_auth.proof_for_port(5000) != first


def test_the_ready_proof_is_the_value_the_launcher_expects(monkeypatch):
    """The same vector is in desktop/test/backend.test.js: the two sides must agree
    byte for byte or the launcher will never believe the backend."""
    monkeypatch.setattr(launch_auth, "_token", ("t" * 43).encode())
    assert launch_auth.proof_for_port(5000) == "8b61da84dde096f8c0ba1345c759dbdb5d241222f4d439efd5527dcbc1b96022"


@pytest.mark.parametrize("token", ["", "short", "a" * 31, "has space " + "a" * 40, "tab\t" + "a" * 40, "é" * 40])
def test_a_weak_or_unsafe_token_is_not_installed(monkeypatch, token):
    monkeypatch.setattr(launch_auth, "_token", None)
    with pytest.raises(launch_auth.InvalidLaunchToken):
        launch_auth.install(token)
    assert launch_auth._token is None


def test_a_token_is_installed_once(monkeypatch):
    monkeypatch.setattr(launch_auth, "_token", None)
    launch_auth.install("a" * 43)
    with pytest.raises(RuntimeError, match="already installed"):
        launch_auth.install("b" * 43)
