"""The warning about a proxy that does not send forwarded headers
(app/server/proxy_check.py). It reports; it never blocks."""

import httpx
import pytest
from starlette.responses import PlainTextResponse

from app.server import proxy_check
from app.server.proxy_check import check_forwarded_headers, missing_headers

PUBLIC = "http://dots.example.com"


async def inner(scope, receive, send):
    if scope["type"] == "http":
        await PlainTextResponse("ok")(scope, receive, send)


@pytest.fixture(autouse=True)
def trusted_public_name(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "TRUSTED_HOSTS", "localhost,127.0.0.1,[::1],dots.example.com")


@pytest.fixture
def warnings(monkeypatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(proxy_check, "warn", seen.append)
    return seen


def client(base_url=PUBLIC, headers=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(check_forwarded_headers(inner)), base_url=base_url, headers=headers or {}
    )


def scope(host: str, *headers: str) -> dict:
    pairs = [(b"host", host.encode())] + [(h.lower().encode(), b"x") for h in headers]
    return {"type": "http", "headers": pairs}


async def test_a_public_name_without_forwarded_headers_is_reported_once_per_problem(warnings):
    async with client() as api:
        for _ in range(3):
            assert (await api.get("/")).text == "ok"  # never blocked
    assert len(warnings) == 2
    assert "X-Forwarded-For" in warnings[0] and "dots.example.com" in warnings[0] and "lockout" in warnings[0]
    assert "X-Forwarded-Proto" in warnings[1] and "Secure" in warnings[1]


async def test_a_proxy_that_sends_both_is_not_reported(warnings):
    async with client(headers={"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}) as api:
        await api.get("/")
    assert warnings == []


async def test_only_the_missing_header_is_named(warnings):
    async with client(headers={"X-Forwarded-For": "203.0.113.9"}) as api:
        await api.get("/")
    assert len(warnings) == 1 and "X-Forwarded-Proto" in warnings[0]


async def test_the_standard_forwarded_header_does_not_count_because_the_server_ignores_it(warnings):
    async with client(headers={"Forwarded": "for=203.0.113.9;proto=https"}) as api:
        assert (await api.get("/")).text == "ok"  # still never blocked
    assert len(warnings) == 2
    for message, name in zip(warnings, ("X-Forwarded-For", "X-Forwarded-Proto")):
        assert f"without {name}" in message and "standard `Forwarded` header" in message and "does not read it" in message


async def test_the_hint_about_forwarded_appears_only_when_that_header_was_sent(warnings):
    async with client() as api:
        await api.get("/")
    assert warnings and not any("Forwarded` header" in message for message in warnings)


async def test_forwarded_does_not_hide_the_one_header_that_is_missing(warnings):
    async with client(headers={"Forwarded": "for=203.0.113.9", "X-Forwarded-For": "203.0.113.9"}) as api:
        await api.get("/")
    assert len(warnings) == 1 and "without X-Forwarded-Proto" in warnings[0] and "does not read it" in warnings[0]


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"])
async def test_local_use_is_never_reported(warnings, base):
    async with client(base_url=base) as api:
        await api.get("/")
    assert warnings == []


async def test_a_name_the_server_does_not_answer_to_is_not_reported_or_copied_into_the_log(warnings):
    async with client(base_url="http://evil.example.net") as api:
        await api.get("/")
    assert warnings == []


def test_a_request_with_no_host_or_a_repeated_host_is_not_this_checks_business():
    assert missing_headers({"type": "http", "headers": []}) == []
    assert missing_headers({"type": "http", "headers": [(b"host", b"a.example"), (b"host", b"b.example")]}) == []


def test_other_scope_types_are_ignored():
    assert missing_headers({"type": "lifespan"}) == []
    assert missing_headers({"type": "websocket", "headers": []}) == []


def test_the_check_names_exactly_what_is_missing():
    assert missing_headers(scope("dots.example.com")) == ["X-Forwarded-For", "X-Forwarded-Proto"]
    assert missing_headers(scope("dots.example.com", "x-forwarded-for")) == ["X-Forwarded-Proto"]
    assert missing_headers(scope("dots.example.com", "x-forwarded-proto")) == ["X-Forwarded-For"]
    # The standard header carries the same facts but the server does not read it, so it counts for nothing.
    assert missing_headers(scope("dots.example.com", "forwarded")) == ["X-Forwarded-For", "X-Forwarded-Proto"]
    assert missing_headers(scope("dots.example.com", "forwarded", "x-forwarded-for", "x-forwarded-proto")) == []


async def test_the_server_application_is_wrapped_by_the_check(tmp_path, warnings, monkeypatch):
    from app.core.config import settings
    from app.server.entry import ServerOptions, build_app

    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setattr(settings, "TRUSTED_HOSTS", "localhost,127.0.0.1,[::1],dots.example.com")
    app = build_app(ServerOptions(host="127.0.0.1", port=8000, ui_dir=tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=PUBLIC) as api:
        assert (await api.get("/")).status_code == 200  # a page
        assert (await api.get("/api/v1/health")).status_code == 200  # the API
    assert len(warnings) == 2
    assert "without X-Forwarded-For" in warnings[0] and "without X-Forwarded-Proto" in warnings[1]
